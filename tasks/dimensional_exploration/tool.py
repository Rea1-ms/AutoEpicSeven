from module.config.config import AzurLaneConfig


def run_tool(config_name: str) -> None:
    """Run one manual batch without entering the main scheduler."""
    config = AzurLaneConfig(config_name, task="DimensionalExploration")

    from module.device.device import Device

    device = Device(config=config)

    # Device initialization resolves automatic package detection and selects
    # the asset language. Import the task afterwards so server-specific pages
    # are registered for this profile, not the default CN server.
    from tasks.dimensional_exploration.dimensional_exploration import DimensionalExploration

    DimensionalExploration(config=config, device=device).run()
