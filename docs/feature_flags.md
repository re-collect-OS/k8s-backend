# Feature Flags Implementation Guide

Table of contents:

1. [Introduction](#introduction)
2. [Categories of Feature Flags](#categories-of-feature-flags)
3. [Contract and implementation notes](#contract-and-implementation-notes)
4. [Creating a new feature flag in LaunchDarkly](#creating-a-new-feature-flag-in-launchdarkly)
5. [Code examples](#code-examples)
    1. [A targeted Experiment Feature](#a-targeted-experiment-feature)
    2. [A simple Killswitch Feature](#a-simple-killswitch-feature)
    3. [A more complex Operational Feature](#a-more-complex-operational-feature)

## Introduction

Feature flags (or toggles) are mechanisms that allow dynamic control (e.g. activation/deactivation, updating values, etc.) of application logic without requirement a deployments of new code.
Their primary purpose is to facilitate a more controlled and flexible approach to releasing, testing, and managing features in a software application.

Feature flags serve several key purposes:

- **Controlled Rollouts**: Incrementally release of new features, minimize risk and enable a phased approach to user exposure.
- **A/B Testing**: Allow comparison of different feature sets among different user segments, used to obtain insights into user preferences and behavior.
- **Operational Flexibility/Mitigation**: Enable or disable features and dynamically adjust settings in response to operational requirements and/or performance issues.

## Categories of Feature Flags

This project's usage of Feature Flags follows Martin Fowler's [Feature Toggles](https://martinfowler.com/articles/feature-toggles.html) article, which defines five categories:

1. **Release**: A feature toggle used to enable/disable codepaths for unfinished features. Allows deploying to production without exposing unfinished features to users. Typical lifetime of such toggles is ~30 days.

2. **Experiment**: A feature toggle used to perform multivariate or A/B testing, determining which of two codepaths activates for a given user. Typical lifetime is ~30 days.

3. **Operational**: A feature toggle used to control operational aspects of the system's behavior. Allows quick experimentation of infrastruction settings (timeouts, retries, delays, etc.) until an optimal configuration is found. Typical lifetime is 7-30 days.

4. **Killswitch**: A killswitch is a particular type of operational toggle that can be seen as a manually-managed circuit breaker. These features are boolean and take no further context information — each killswitch should target one specific, narrow piece of functionality (e.g. disable a specific API endpoint, load shedding, etc.) Killswitches are expected to be permanent.

5. **Permission**: A feature toggle used to change the features or product experience that certain users receive. Permission flags are expected to be permanent.

## Contract and implementation notes

The [contract](../src/common/features/features.py) was designed to be straightforward to port between different providers. The current implementation is backed by LaunchDarkly — backed by their service in Staging/Production environments and by a [local file](../dev/feature-flags-template.yaml) in local Development environments.

## Creating a new feature flag in LaunchDarkly

Log into LaunchDarkly and click "Features Flags" in the left pane. You'll be shown a modal that presents you with a few options or you can just go ahead and click "Custom flag" — you'll end up reviewing advanced properties anyway.

Flag settings:
- **Name:** a human-readable name.
- **Key**: unique identifier for this flag. Should be prefixed with the category, e.g. `release.foo` or `killswitch.bar`
- **Description:** a meaningful description of the purpose of the flag — be detailed!
- **Flag type:** "Boolean" for all flags except `operational`
- **Variations:** "Boolean" flags only allow for two variations; `operational` flags allow for multiple variations so populate accordingly.
- **Default variations:** default value to serve when targeting rules are off (only `experiment` and `permission` flags support targeting).
- **Temporary**: "Yes" for all flags except `killswitch` and `permission`
- **Tags:** used for filtering; always tag with the category of flag (i.e. `release`, `experiment`, `operational`, `killswitch`, `permission`) and add more relevant tags such as which type of app the flag applies to (e.g. `worker`, `http-server`), as well as the specific service it applies to if relevant (e.g. `external-api`, `account-deleter`).
- **Client-side availability:** leave disabled.

> [!IMPORTANT]
> Whenever you create a new flag in LaunchDarkly, be sure to also add it to the [feature-flags-template.yaml](../dev/feature-flags-template.yaml) file. This file is used to populate the local Development environment but also serves as as a reference for the Staging/Production environments.

## Code examples

### A targeted Experiment feature

```python
from common import features
from common.features import Experiment


class Lottery:
    def __init__(self, experiment: features.Experiment):
        self._lottery = experiment

    def try_luck(self, user: str) -> str:
        can_play = self._lottery.is_enabled(user)
        if not can_play:
            return "sorry mate, you can't play :("

        if random.randint(0, 100) < 50:
            return "winner winner, chicken dinner!"
        else:
            return "better luck next time :("


if __name__ == "__main__":
    experiment = features.get().experiment(key="lottery")
    # Pass only the Experiment to the Foo class, not Features.
    # In general, we heavily favor explicit, narrow dependencies.
    lottery = Lottery(experiment)

    # Assumes "experiment.lottery" has targetting enabled in LaunchDarkly and
    # that the user "winner@re-collect" is targeted for the "enabled" variation.
    print(lottery.try_luck("winner@re-collect.ai"))
    print(lottery.try_luck("loser@re-collect.ai"))
```

### Adding a Killswitch Flag to the Lottery

```python
from common import features
from common.features import Experiment


class Lottery:
    def __init__(
        self,
        experiment: features.Experiment,
        killswitch: features.Killswitch,
    ):
        self._lottery = experiment
        self._killswitch = killswitch

    def try_luck(self, for_user: str) -> str:
        if self._killswitch.is_enabled():
            return "no lottery today, try again tomorrow :("

        can_play = self._lottery.is_enabled(for_user)
        if not can_play:
            return "sorry mate, you can't play :("

        if random.randint(0, 100) < 50:
            return "winner winner, chicken dinner!"
        else:
            return "better luck next time :("


if __name__ == "__main__":
    # Avoid calling features.get() multiple times (especially in performance
    # sensitive code) as it requires locks to ensure only one LDClient instance.
    features = features.get()

    # Maps to "experiment.lottery" flag key in launchdarkly
    experiment = features.experiment(key="lottery")
    # Maps to "killswitch.lottery" flag key in launchdarkly
    killswitch = features.killswitch(key="lottery")

    lottery = Lottery(experiment, killswitch)
    print(lottery.try_luck("winner@re-collect.ai"))
    print(lottery.try_luck("loser@re-collect.ai"))
```

### Controlling the winning probability via Operational Flag

```python
from dataclasses import dataclass

from common import features
from common.features import Experiment


@dataclass
class LotteryConfig:
    winning_probability: int  # 0-100


class Lottery:
    def __init__(
        self,
        experiment: features.Experiment,
        config: features.Operational,
    ):
        self._lottery = experiment
        self._config = config

    def try_luck(self, user: str) -> str:
        can_play = self._lottery.is_enabled(user)
        if not can_play:
            return "sorry mate, you can't play :("

        winning_probability = self._config.get().winning_probability
        if random.randint(0, 100) < winning_probability:
            return "winner winner, chicken dinner!"
        else:
            return "better luck next time :("


if __name__ == "__main__":
    features = features.get()
    experiment = features.experiment(key="lottery")
    config = features.operational(
        key="lottery-cfg",
        type_cls=LotteryConfig,
        default=LotteryConfig(1), # default to 1% chance of winning
    )
    lottery = Lottery(experiment, config)

    print(lottery.try_luck("winner@re-collect.ai"))
    print(lottery.try_luck("loser@re-collect.ai"))
```
