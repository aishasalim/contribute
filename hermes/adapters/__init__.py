from hermes.adapters.ashby import Ashby
from hermes.adapters.greenhouse import Greenhouse
from hermes.adapters.lever import Lever

ADAPTERS = (Greenhouse(), Lever(), Ashby())


def adapter_for(url: str):
    return next((adapter for adapter in ADAPTERS if adapter.matches(url)), None)
