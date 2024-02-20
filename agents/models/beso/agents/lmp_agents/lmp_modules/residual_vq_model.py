import logging
from typing import Dict, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.distributions as D
import hydra
from omegaconf import DictConfig
import einops 
import wandb
from pytorch_memlab import MemReporter
from vector_quantize_pytorch import ResidualVQ

from beso.agents.lmp_agents.lmp_modules.utils import State
from beso.agents.lmp_agents.lmp_modules.plan_encoders.vqvae_plan_recognition_net import Codebook, EMACodebook, SQEmbedding

logger = logging.getLogger(__name__)


class VqPlayLMP(nn.Module):
    def __init__(
        self,
        plan_proposal: DictConfig,
        plan_recognition: DictConfig,
        action_decoder: DictConfig,
        state_reconstruction: DictConfig,
        beta: float,
        embedding_dim: int,
        num_embeddings: int,
        decay: float,
        q_loss_factor: float,
        lr: float,
        eps: float,
        betas: Tuple[float, float],
        replan_freq: int = 30,
        device: str = 'cuda',
        use_goal_in_recognition: bool = False,
        use_state_reconstruction: bool = False,
    ):
        super(VqPlayLMP, self).__init__()
        self.device = device
        self.plan_proposal = hydra.utils.instantiate(plan_proposal).to(self.device) #VQ_VAE_GPT 
        self.plan_recognition = hydra.utils.instantiate(plan_recognition).to(self.device) # VQVAE 
        self.action_decoder = hydra.utils.instantiate(action_decoder).to(self.device)
        self.state_reconstruction = hydra.utils.instantiate(state_reconstruction).to(self.device)
        self.q_loss_factor = q_loss_factor
        self.decay = decay
        self.use_state_reconstruction = use_state_reconstruction
        #vq vae 
        self.codebook = ResidualVQ(
            dim=embedding_dim,
            codebook_size=num_embeddings,
            decay = 0.8,             # the exponential moving average decay, lower means the dictionary will change faster
            commitment_weight = 1.,   # the weight on the commitment loss
            kmeans_init = True,   # set to True
            kmeans_iters = 10,     # number of kmeans iterations to calculate the centroids for the codebook on init
            sample_codebook_temp = 0.1, # temperature for stochastically sampling codes, 0 would be equivalent to non-stochastic
            shared_codebook = True,      # whether to share the codebooks for all quantizers or not
        )
        self.plan_features = embedding_dim
        # configure optimizers
        self.opt_vq, self.opt_prop, self.opt_act = self.configure_optimizers(lr, eps, betas)
        
        # for inference
        self.rollout_step_counter = 0
        self.replan_freq = replan_freq
        self.latent_goal = None
        self.plan = None
        # weight for kl loss 
        self.use_goal_in_recognition = use_goal_in_recognition
        self.epoch = 0
        self.steps = 0
    
    def configure_optimizers(self, lr, eps, betas):
        '''
        Define optimizers for the different modules
        '''
        opt_vq = torch.optim.Adam(
            list(self.plan_recognition.parameters()) + 
            list(self.action_decoder.parameters())+
            list(self.state_reconstruction.parameters()),
            # list(self.codebook.parameters()), 
            lr=lr, eps=eps, betas=betas)
        opt_prop = torch.optim.AdamW(self.plan_proposal.parameters(), lr=lr, betas=betas)
        
        opt_act = torch.optim.Adam(self.action_decoder.parameters(), lr=lr, eps=eps, betas=betas)
        return opt_vq, opt_prop, opt_act
    
    def encode_plan(self, x):
        '''
        Encode the plan using the plan recognition model with the VQ-VAE
        '''
        encoded_states = self.plan_recognition(x)
        quant_states, codebook_indices, q_loss = self.codebook(encoded_states)
        return quant_states, codebook_indices, q_loss
    
    def decode_plan(self, z):
        '''
        Decode the plan using the plan recognition model with the VQ-VAE
        '''
        decoded_states = self.decoder(z)
        return decoded_states
    
    def compute_loss(self, states, actions, goal):
        '''
        Method to compute the loss for the VQ-VAE recognition model during training
        '''
        # plan recognition
        if self.use_goal_in_recognition:
            state_sequence = torch.cat([states, goal], dim=1)
        else:
            state_sequence = states
            
        quant_plan, codebook_indices, q_loss = self.encode_plan(state_sequence)
        
        # state reconstruction
        if self.use_state_reconstruction:
           first_state_loss,  goal_loss = self.state_reconstruction.compute_reconstruction_losses(quant_plan, states, goal)
        else:
            goal_loss, first_state_loss = None, None
        # action decoder
        action_loss, pred_actions = self.action_decoder.loss_and_act(quant_plan, states, goal, actions)
        
        reconstruction_loss = action_loss
        if self.use_state_reconstruction:
            reconstruction_loss += goal_loss + first_state_loss 
        
        vq_loss = reconstruction_loss + q_loss * self.q_loss_factor

        # log losses to wandb
        total_loss = 0
        if reconstruction_loss is not None:
            wandb.log({"training/reconstruction_loss": reconstruction_loss})
            total_loss += reconstruction_loss.item()
        if q_loss is not None:
            wandb.log({"training/q_loss": q_loss.item()})
            total_loss += q_loss.item()
        if goal_loss is not None:
            total_loss += goal_loss.item()
            wandb.log({"training/goal_loss": goal_loss})
        if first_state_loss is not None:
            wandb.log({"training/first_state_loss": first_state_loss})
            total_loss += first_state_loss.item()
        wandb.log({"training/total_loss": total_loss})
        
        self.opt_vq.zero_grad()
        vq_loss.backward()
        self.opt_vq.step()
        
        return total_loss

    def compute_finetune_loss(self, states, actions, goal):
        '''
        Method to compute the finetuning loss for the VQ-VAE recognition model during the second
        phase of training for training the plan proposal network
        '''
        self.codebook.eval()
        first_state = einops.rearrange(states[:, 0, :], 'b d -> b 1 d')
        
        if self.use_goal_in_recognition:
            state_sequence = torch.cat([states, goal], dim=1)
        else:
            state_sequence = states
            
        quant_plan, codebook_indices, q_loss = self.encode_plan(state_sequence)
        
        #  gpt proposal loss
        logits, targets = self.plan_proposal(first_state, goal, codebook_indices)
        # indices = self.plan_proposal.sample(first_state, goal, steps=1)
        
        # sampled_plan = self.codebook.embedding(indices)
        # reconstruction_loss, pred_actions = self.action_decoder.loss_and_act(sampled_plan, states, goal, actions)
        proposal_loss = F.cross_entropy(logits.reshape(-1, logits.size(-1)), codebook_indices.reshape(-1))
        proposal_loss.backward()
        self.opt_prop.step()
        
        total_loss = proposal_loss  # + reconstruction_loss
        wandb.log({"fine_tuning/proposal_loss": proposal_loss})
        
        return total_loss.item()
    
    @torch.no_grad()
    def compute_val_loss(self, states, actions, goal):
        '''
        Method to compute the loss for the VQ-VAE recognition model during validation
        '''
        if self.use_goal_in_recognition:
            state_sequence = torch.cat([states, goal], dim=1)
        else:
            state_sequence = states
            
        quant_plan, codebook_indices, q_loss, _ = self.encode_plan(state_sequence)
        
        # action decoder  reconstruction loss
        reconstruction_loss, pred_actions = self.action_decoder.loss_and_act(quant_plan, states, goal, actions)
        return reconstruction_loss
    
    @torch.no_grad()
    def compute_tuning_val_loss(self, states, actions, goal):
        '''
        Method to compute the loss for the VQ-VAE recognition model during validation in the second phase
        '''
        first_state = einops.rearrange(states[:, 0, :], 'b d -> b 1 d')
    
        indices = self.plan_proposal.sample(first_state, goal, steps=1)
        sampled_plan = self.codebook.get_codes_from_indices(indices)
        # sampled_plan = self.codebook.get_codes_from_indices[indices]
        # action decoder prediction
        action_loss, pred_actions = self.action_decoder.loss_and_act(sampled_plan, states, goal, actions)
        return action_loss 
    
    def reset(self):
        self.plan = None
        self.latent_goal = None
        self.rollout_step_counter = 0
    
    @torch.no_grad()
    def step(self, state, goal):
        '''
        Method to take a step in the environment.
        '''
        if self.rollout_step_counter % self.replan_freq == 0:
        # replan every replan_freq steps (default 30 i.e every second)
            self.plan = self.get_pp_plan(state, goal)
            self.latent_goal = goal
        # use plan to predict actions with current observations
        # action decoder
        action = self.action_decoder.act(self.plan, state, self.latent_goal)
        self.rollout_step_counter += 1
        return action, self.plan

    def freeze_model_weights(self):
        '''
        Freeze the weights of the model during fine tuning.
        '''
        self.plan_recognition.requires_grad_(False)
        # self.action_decoder.requires_grad_(False)
    
    @torch.no_grad()
    def get_pp_plan(self, state, goal):
        '''
        Method to get a plan from the proposal network.
        '''
        indices = self.plan_proposal.sample(state, goal, steps=1)
        sampled_plan = self.codebook.get_codes_from_indices(indices)
        return sampled_plan

    def predict_with_plan(
        self,
        obs: Dict[str, Dict],
        latent_goal: torch.Tensor,
        sampled_plan: torch.Tensor,
    ) -> torch.Tensor:
        with torch.no_grad():
            perceptual_emb = self.perceptual_encoder(obs["rgb_obs"], obs["depth_obs"], obs["robot_obs"])
            action = self.action_decoder.act(sampled_plan, perceptual_emb, latent_goal)

        return action

    def get_params(self):
        return self.parameters()

    def load_checkpoint(self, checkpoint_path):
        checkpoint = torch.load(checkpoint_path)
        self.load_state_dict(checkpoint['model_state_dict'])
        print(f"Loaded checkpoint from {checkpoint_path}")
    
