import os
import shutil
import subprocess
from urllib.parse import urlparse

from deploy.Windows.config import DeployConfig, ExecutionError
from deploy.Windows.logger import logger, Progress
from deploy.Windows.utils import cached_property


class PipManager(DeployConfig):
    @cached_property
    def uv(self) -> str:
        if self.UvExecutable:
            configured = self.filepath(self.UvExecutable)
            if os.path.isfile(configured):
                return configured
            executable = shutil.which(self.UvExecutable)
            if executable:
                return executable
            logger.critical(f'UvExecutable does not exist: {configured}')
            raise ExecutionError

        candidates = [
            self.filepath('./toolkit/uv.exe'),
            self.filepath('./toolkit/Scripts/uv.exe'),
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate

        executable = shutil.which('uv')
        if executable:
            return executable

        logger.critical('uv is required to install dependencies')
        raise ExecutionError

    def uv_execute(self, args: list[str]) -> None:
        command = [self.uv, *args]
        logger.info(subprocess.list2cmdline(command))
        try:
            subprocess.run(command, cwd=self.root_filepath, check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            logger.info(f'[ failure ] {error}')
            self.show_error(subprocess.list2cmdline(command))
            raise ExecutionError from error
        logger.info('[ success ]')

    def pip_install(self):
        logger.hr('Update Dependencies', 0)

        if not self.InstallDependencies:
            logger.info('InstallDependencies is disabled, skip')
            Progress.UpdateDependency()
            return

        logger.hr('Check Python', 1)
        self.execute(f'"{self.python}" --version')

        requirements = self.requirements_file
        if self.RequirementsFile == 'requirements.txt':
            logger.hr('Export Locked Dependencies', 1)
            self.uv_execute([
                'export',
                '--frozen',
                '--no-dev',
                '--no-hashes',
                '--no-emit-project',
                '--no-annotate',
                '--output-file',
                requirements,
            ])

        args = [
            'pip',
            'sync',
            requirements,
            '--python',
            self.python,
            '--strict',
            '--no-python-downloads',
        ]
        if self.PypiMirror:
            mirror = self.PypiMirror
            args += ['--default-index', mirror]
            # Trust http mirror or skip ssl verify
            if 'http:' in mirror or not self.SSLVerify:
                hostname = urlparse(mirror).hostname
                if hostname:
                    args += ['--allow-insecure-host', hostname]
        elif not self.SSLVerify:
            args += ['--allow-insecure-host', 'pypi.org']
            args += ['--allow-insecure-host', 'files.pythonhosted.org']

        logger.hr('Update Dependencies', 1)
        self.uv_execute(args)
        Progress.UpdateDependency()
