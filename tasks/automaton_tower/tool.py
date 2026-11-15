"""Standalone WebUI entry point for the Automaton Tower tool."""

from module.config.config import AzurLaneConfig


def run_tool(config_name: str) -> None:
    """Run the tower from Tools without entering the main scheduler."""
    config = AzurLaneConfig(config_name, task="AutomatonTower")

    from module.device.device import Device

    device = Device(config=config)

    # Device initialization resolves the account server and asset language.
    # Import the task afterwards so server-specific pages use that profile
    # instead of the default server selected during module import.
    from tasks.automaton_tower.automaton_tower import AutomatonTower

    AutomatonTower(config=config, device=device, task="AutomatonTower").run()
