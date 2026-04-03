"""Episode runner."""

from simulator.env import MobilityEnv
from simulator.models.result import EpisodeResult
from simulator.policies.base import BasePolicy


def run_episode(env: MobilityEnv, policy: BasePolicy) -> EpisodeResult:
    state = env.reset()
    policy.reset()

    while not env.is_done():
        state = env.get_state()
        valid_actions = env.get_valid_actions()
        action = policy.select_action(state, valid_actions)
        state, _, _, _ = env.step(action)

    return env.get_result(policy_name=policy.get_name())
