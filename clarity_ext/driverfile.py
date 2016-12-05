import os
import shutil
import difflib
from genologics.epp import attach_file
from genologics.entities import *
from clarity_ext.utils import lazyprop
import re
import codecs


class OSService(object):
    def __init__(self):
        pass

    def exists(self, path):
        return os.path.exists(path)

    def makedirs(self, path):
        os.makedirs(path)

    def open_file(self, path, mode):
        return open(path, mode)

    def rmdir(self, path):
        os.rmdir(path)

    def mkdir(self, path):
        os.mkdir(path)

    def copy_file(self, source, dest):
        shutil.copyfile(source, dest)

    def attach_file_for_epp(self, local_file, artifact):
        return attach_file(local_file, artifact)


class GeneralFileService(object):
    def __init__(self, specific_file_service, result_path, os_service):
        self.logger = specific_file_service.logger or \
                      logging.getLogger(__name__)
        self.extension = specific_file_service.extension
        self.result_path = result_path
        self.specific_file_service = specific_file_service
        self.os_service = os_service

    def execute(self, commit=False, artifacts_to_stdout=False):
        """
        :param commit: Set to True to write back to the LIMS. Set to False while testing, which only
           moves the file to the ./uploaded directory
        :param driver_files_to_stdout: Set to True to write uploaded artifacts to stdout too.
        :return:
        """
        # Save the file to the directory:
        local_file = self._save_file_locally(self.result_path)
        self.specific_file_service.local_file = local_file
        self._upload(local_file, commit, artifacts_to_stdout)

    def _save_file_locally(self, root):
        """Saves the output generated by the instance"""
        if not self.os_service.exists(root):
            self.logger.debug("Creating directories {}".format(root))
            self.os_service.makedirs(root)
        full_path = os.path.join(root, self.extension.filename())
        # The file needs to be opened in binary form to ensure that Windows line endings are used if specified
        with self.os_service.open_file(full_path, 'wb') as f:
            self.logger.debug("Writing output to {}.".format(full_path))
            # Content should be either a string or something else we can iterate over, in which case we need newline
            content = self.specific_file_service.content()
            if isinstance(content, basestring):
                try:
                    f.write(content)
                except UnicodeEncodeError:
                    f.write(content.encode("utf-8"))
            else:
                newline = self.extension.newline()
                for line in content:
                    f.write(line + newline)
        return full_path

    def _upload(self, local_file, commit, artifacts_to_stdout):
        self.specific_file_service.print_log()
        if commit:
            self.specific_file_service.commit()
        else:
            # When not connected to an actual server, we copy the file to another directory for integration tests
            upload_path = os.path.join(self.result_path, "uploaded")
            self.logger.info("Commit is set to false, copying the file to {}".format(upload_path))
            if self.os_service.exists(upload_path):
                self.os_service.rmdir(upload_path)
            self.os_service.mkdir(upload_path)
            # The LIMS does always add a prefix with the artifact ID:
            new_file_name = self.specific_file_service.lims_adapted_file_name()
            new_file_path = os.path.join(upload_path, new_file_name)
            self.os_service.copy_file(local_file, new_file_path)

        if artifacts_to_stdout:
            print self.specific_file_service.header_for_stoutput()
            with self.os_service.open_file(local_file, 'r') as f:
                print f.read()
            print "---"


class DriverFileService:
    def __init__(self, extension, shared_file_name, os_service, logger=None):
        self.extension = extension
        self.logger = logger
        self.local_file = None
        self.os_service = os_service
        self.shared_file_name = shared_file_name

    def commit(self):
        # Find the output on the current step
        self.logger.info("Uploading to the LIMS server")
        self.os_service.attach_file_for_epp(self.local_file, self.artifact)

    def header_for_stoutput(self):
        return "--- {} => {} ({})".format(self.local_file, self.artifact.name, self.artifact.id)

    def lims_adapted_file_name(self):
        return "{}_{}".format(self.artifact.id, os.path.basename(self.local_file))

    def print_log(self):
        self.logger.info("Uploading local file {} to the LIMS placeholder at {}".format(
            self.local_file, self.artifact.id))
        self.logger.debug("Shared files: {}".format(
            [_artifact.name for _artifact in
             self.extension.context.shared_files]))
        self.logger.debug("Shared file from extension: {}".format(
            self.extension.shared_file()))

    def content(self):
        return self.extension.content()

    @lazyprop
    def artifact(self):
        artifacts = [shared_file for shared_file in self.extension.context.shared_files
                     if shared_file.name == self.shared_file_name]
        assert len(artifacts) == 1, "Shared file '{}' not exactly single".format(
            self.shared_file_name, ",".join("'{}'".format(f.name) for f in self.extension.context.shared_files))
        return artifacts[0]

    @staticmethod
    def create_file_service(instance, shared_file_name, logger, os_service):
        driver_file_service = DriverFileService(instance, shared_file_name, os_service, logger)
        return GeneralFileService(driver_file_service, ".", os_service)


class ResponseFileService:
    def __init__(self, extension, logger=None):
        self.extension = extension
        self.logger = logger
        self.local_file = None

    def commit(self):
        """Do nothing"""
        pass

    def header_for_stoutput(self):
        return "--- {}".format(self.local_file)

    def lims_adapted_file_name(self):
        return "{}_{}".format(self.extension.context.session.current_step_id, os.path.basename(self.local_file))

    def content(self):
        self.extension.execute()
        for row in self.extension.context.response:
            yield "\t".join(row)

    def print_log(self):
        pass

    @staticmethod
    def create_file_service(instance, logger, os_service):
        response_file_svc = ResponseFileService(instance, logger)
        return GeneralFileService(response_file_svc, ".", os_service)


class DriverFileIntegrationTests(object):
    @staticmethod
    def _locate_driver_file_pair(run_directory, frozen_directory, test):
        def locate_driver_file(path):
            files = os.listdir(path)
            count = len(files)
            if count != 1:
                raise UnexpectedNumberOfFilesException("{}: {}".format(path, count))

            for file_name in files:
                import fnmatch
                if fnmatch.fnmatch(file_name, "{}*".format(test["out_file"])):
                    return os.path.join(path, file_name)
                else:
                    raise FrozenFileNotFoundException("No frozen file found")

        frozen_path = os.path.join(frozen_directory, "uploaded")
        run_path = os.path.join(run_directory, "uploaded")

        # We want to find one file (can currently only be one) and it should
        # start with the step name. The rest of the file name can be anything and is not
        # tested here
        frozen_file = locate_driver_file(frozen_path)
        run_file = locate_driver_file(run_path)
        return frozen_file, run_file

    def validate(self, run_directory, frozen_directory, test):
        pair = self._locate_driver_file_pair(run_directory, frozen_directory, test)
        fromfile, tofile = pair
        fromlines = open(fromfile, 'r').readlines()
        tolines = open(tofile, 'r').readlines()
        diff = list(difflib.unified_diff(fromlines, tolines, fromfile, tofile))
        if len(diff) > 0:
            raise FilesDifferException("Diff (max 100 lines):\n{}".format("".join(diff[0:100])))


class FilesDifferException(Exception):
    pass


class FrozenFileNotFoundException(Exception):
    pass


class UnexpectedNumberOfFilesException(Exception):
    pass


