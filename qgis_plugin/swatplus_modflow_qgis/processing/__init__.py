"""Processing algorithms of the plugin."""
from .channel_connections import ChannelConnectionsAlgorithm
from .create_grid import CreateGridAlgorithm
from .hru_connections import HruConnectionsAlgorithm
from .validate_inputs import ValidateInputsAlgorithm

ALGORITHMS = [CreateGridAlgorithm, HruConnectionsAlgorithm, ChannelConnectionsAlgorithm, ValidateInputsAlgorithm]
