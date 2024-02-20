python run.py --config-name=pushing_config \
              --multirun seed=0,1,2,3,4,5 \
              agents=bet_mlp_agent \
              agent_name=bet_mlp \
              window_size=1 \
              group=aligning_bet_mlp_seeds \
              simulation.n_cores=10 \
              simulation.n_contexts=30 \
              simulation.n_trajectories_per_context=16 \
              agents.model.vocab_size=16 \
              agents.model.offset_loss_scale=1.0