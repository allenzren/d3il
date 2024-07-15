from gym.envs.registration import register

register(
    id="avoiding-v0",
    entry_point="gym_avoiding.envs:ObstacleAvoidanceEnv",
    max_episode_steps=150,
)


register(
    id="avoiding-m5",
    entry_point="gym_avoiding.envs:ObstacleAvoidanceM5Env",
    max_episode_steps=150,
)

register(
    id="avoiding-m6",
    entry_point="gym_avoiding.envs:ObstacleAvoidanceM6Env",
    max_episode_steps=150,
)

register(
    id="avoiding-m7",
    entry_point="gym_avoiding.envs:ObstacleAvoidanceM7Env",
    max_episode_steps=150,
)

register(
    id="avoiding-m8",
    entry_point="gym_avoiding.envs:ObstacleAvoidanceM8Env",
    max_episode_steps=150,
)