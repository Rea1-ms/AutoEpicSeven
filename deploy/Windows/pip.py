from urllib.parse import urlparse

from deploy.Windows.config import DeployConfig
from deploy.Windows.logger import logger, Progress
from deploy.Windows.utils import cached_property


class PipManager(DeployConfig):
    @cached_property
    def pip(self):
        return f'"{self.python}" -m pip'

    def pip_install(self):
        logger.hr('Update Dependencies', 0)

        if not self.InstallDependencies:
            logger.info('InstallDependencies is disabled, skip')
            Progress.UpdateDependency()
            return

        # Let pip evaluate platform markers and exact locked versions. The old
        # dist-info filename comparison could silently miss uv-exported entries.
        logger.hr('Check Python', 1)
        self.execute(f'"{self.python}" --version')

        arg = []
        if self.PypiMirror:
            mirror = self.PypiMirror
            arg += ['-i', mirror]
            # Trust http mirror or skip ssl verify
            if 'http:' in mirror or not self.SSLVerify:
                arg += ['--trusted-host', urlparse(mirror).hostname]
        elif not self.SSLVerify:
            arg += ['--trusted-host', 'pypi.org']
            arg += ['--trusted-host', 'files.pythonhosted.org']

        # Don't update pip, just leave it.
        # logger.hr('Update pip', 1)
        # self.execute(f'"{self.pip}" install --upgrade pip{arg}')
        arg += ['--disable-pip-version-check']

        logger.hr('Update Dependencies', 1)
        arg = ' ' + ' '.join(arg) if arg else ''
        self.execute(f'{self.pip} install -r {self.requirements_file}{arg}')
        Progress.UpdateDependency()
