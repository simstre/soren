from app.broker.base import BrokerAdapter
from app.broker.mock_adapter import MockAdapter
from app.config import settings

_broker: BrokerAdapter | None = None


def get_broker() -> BrokerAdapter:
    global _broker
    if _broker is None:
        if settings.broker_mode == "ib":
            from app.broker.ib_adapter import IBAdapter
            _broker = IBAdapter()
        else:
            _broker = MockAdapter()
    return _broker
