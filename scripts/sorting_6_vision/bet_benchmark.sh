MUJOCO_GL=egl python run_vision.py --config-name=sorting_6_vision_config \
              --multirun seed=0,1,2,3,4,5 \
              agents=bet_vision_agent \
              agent_name=bet_vision \
              window_size=5 \
              group=sorting_6_bet_seeds
