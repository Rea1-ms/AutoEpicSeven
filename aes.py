from module.alas import AzurLaneAutoScript
from module.logger import logger


class AutoEpicSeven(AzurLaneAutoScript):
    def restart(self):
        from tasks.login.login import Login
        Login(self.config, device=self.device).app_restart()
        # Cool down Restart task itself after recovery to avoid infinite restart loops.
        self.config.task_delay(server_update=True, task='Restart')

    def start(self):
        from tasks.login.login import Login
        Login(self.config, device=self.device).app_start()

    def stop(self):
        from tasks.login.login import Login
        Login(self.config, device=self.device).app_stop()

    def goto_main(self):
        from tasks.login.login import Login
        from tasks.base.ui import UI
        if self.device.app_is_running():
            logger.info('App is already running, goto main page')
            UI(self.config, device=self.device).ui_goto_main()
        else:
            logger.info('App is not running, start app and goto main page')
            Login(self.config, device=self.device).app_start()
            UI(self.config, device=self.device).ui_goto_main()

    def error_postprocess(self):
        # Exit cloud game to reduce extra fee
        if self.config.is_cloud_game:
            from tasks.login.login import Login
            Login(self.config, device=self.device).app_stop()

    def data_update(self):
        from tasks.item.data_update import DataUpdate
        DataUpdate(config=self.config, device=self.device).run()

    def gacha(self):
        from tasks.gacha.gacha import Gacha
        Gacha(config=self.config, device=self.device, task="Gacha").run()

    def sanctuary_daily(self):
        from tasks.sanctuary.sanctuary import Sanctuary
        Sanctuary(config=self.config, device=self.device, task="SanctuaryDaily").run_daily_task()

    def sanctuary_monthly(self):
        from tasks.sanctuary.sanctuary import Sanctuary
        Sanctuary(config=self.config, device=self.device, task="SanctuaryMonthly").run_monthly_task()

    def knights(self):
        from tasks.knights.knights import Knights
        Knights(config=self.config, device=self.device, task="Knights").run()

    def secret_shop(self):
        from tasks.secret_shop.secret_shop import SecretShop
        SecretShop(config=self.config, device=self.device).run()

    def pets(self):
        from tasks.pets.pets import Pets
        Pets(config=self.config, device=self.device, task="Pets").run()

    def pets_gift(self):
        from tasks.pets.pets_gift import PetsGift
        PetsGift(config=self.config, device=self.device, task="PetsGift").run()

    def store(self):
        from tasks.store.store import Store
        Store(config=self.config, device=self.device, task="Store").run()

    def mission_reward(self):
        from tasks.mission_reward.mission_reward import MissionReward
        MissionReward(config=self.config, device=self.device, task="MissionReward").run()

    def mail(self):
        from tasks.mail.mail import Mail
        Mail(config=self.config, device=self.device, task="Mail").run()

    def arena(self):
        from tasks.arena.arena import Arena
        Arena(config=self.config, device=self.device, task="Arena").run()

    def combat(self):
        from tasks.combat.combat import Combat
        Combat(config=self.config, device=self.device, task="Combat").run()

    def benchmark(self):
        from module.daemon.benchmark import run_benchmark
        run_benchmark(config=self.config)


if __name__ == '__main__':
    app = AutoEpicSeven('alas')
    app.loop()
