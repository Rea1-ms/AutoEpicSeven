from module.config.config import AzurLaneConfig


def run_tool(config_name: str) -> None:
    """Run a single manual purchase session, after selecting the device server."""
    config = AzurLaneConfig(config_name, task='BuyPenguins')
    from module.device.device import Device
    device = Device(config=config)
    from tasks.sanctuary.buy_penguins import BuyPenguins
    BuyPenguins(config=config, device=device).run()
