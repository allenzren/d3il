python run.py --config-name=sorting_4_config \
              --multirun seed=0,1,2,3,4,5 \
              agents=ddpm_agent \
              agent_name=ddpm \
              window_size=1 \
              group=sorting_4_ddpm_seeds \
              simulation.n_cores=60 \
              simulation.n_contexts=60 \
              simulation.n_trajectories_per_context=18 \
              agents.model.model.t_dim=8 \
              agents.model.n_timesteps=4