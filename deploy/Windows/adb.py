from deploy.Windows.emulator import EmulatorManager
from deploy.Windows.logger import Progress, logger


def show_fix_tip(module):
    logger.info(f"""
    To fix this:
    1. Open console.bat
    2. Execute the following commands:
        pip uninstall -y {module}
        pip install --no-cache-dir {module}
    3. Re-open Alas.exe
    """)


class AdbManager(EmulatorManager):
    def adb_install(self):
        logger.hr('Start ADB service', 0)

        if self.ReplaceAdb:
            logger.hr('Replace ADB', 1)
            self.adb_replace()
            Progress.AdbReplace()
        if self.AutoConnect:
            logger.hr('ADB Connect', 1)
            self.brute_force_connect()
            Progress.AdbConnect()
