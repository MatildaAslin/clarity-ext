import os
from clarity_ext.domain import Plate, Analyte, DilutionScheme
import importlib
from utils import lazyprop
import shutil
import difflib
from genologics.config import BASEURI, USERNAME, PASSWORD
from genologics.lims import Lims
from genologics.epp import attach_file
from genologics.entities import *
from clarity_ext.extension_context import ExtensionContext


class DriverFileService:
    def __init__(self, extension, result_path, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self.extension = extension
        self.result_path = result_path

    def execute(self, commit=False, artifacts_to_stdout=False):
        """
        :param commit: Set to True to write back to the LIMS. Set to False while testing, which only
           moves the file to the ./uploaded directory
        :param driver_files_to_stdout: Set to True to write uploaded artifacts to stdout too.
        :return:
        """
        # Save the file to the directory:
        local_file = self._save_file_locally(self.result_path)
        self._upload(local_file, commit, artifacts_to_stdout)

    def _save_file_locally(self, root):
        """Saves the output generated by the instance"""
        if not os.path.exists(root):
            self.logger.debug("Creating directories {}".format(root))
            os.makedirs(root)
        full_path = os.path.join(root, self.extension.filename())
        with open(full_path, 'w') as f:
            self.logger.debug("Writing output to {}.".format(full_path))
            for line in self.extension.content():
                f.write(line + "\n")
        return full_path

    def _upload(self, local_file, commit, artifacts_to_stdout):
        artifacts = [shared_file for shared_file in self.extension.context.shared_files
                    if shared_file.name == self.extension.shared_file()]
        assert len(artifacts) == 1
        artifact = artifacts[0]

        self.logger.info("Uploading local file {} to the LIMS placeholder at {}".format(local_file, artifact.id))
        #output_file_resource = self._get_output_file_resource()
        if commit:
            # Find the output on the current step
            self.logger.info("Uploading to the LIMS server")
            attach_file(local_file, artifact)
        else:
            # When not connected to an actual server, we copy the file to another directory for integration tests
            upload_path = os.path.join(self.result_path, "uploaded")
            self.logger.info("Commit is set to false, copying the file to {}".format(upload_path))
            if os.path.exists(upload_path):
                os.rmdir(upload_path)
            os.mkdir(upload_path)
            # The LIMS does always add a prefix with the artifact ID:
            new_file_name = "{}_{}".format(artifact.id, os.path.basename(local_file))
            new_file_path = os.path.join(upload_path, new_file_name)
            shutil.copyfile(local_file, new_file_path)

        if artifacts_to_stdout:
            print "--- {} => {}".format(local_file, artifact.id)
            with open(local_file, 'r') as f:
                print f.read()
            print "---"

    def _get_output_file_resource(self):
        outputs = list(self.current_step.all_outputs())
        output_file_resources = [output for output in outputs if output.id == self.lims_file]
        assert len(output_file_resources) <= 1
        if len(output_file_resources) == 0:
            available = [output_file.id for output_file in outputs]
            message = "Output file '{}' not found. Available IDs on the step: {}" \
                .format(self.lims_file, ", ".join(available))
            raise OutputFileNotFound(message)
        return output_file_resources[0]


class DriverFileIntegrationTests:
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
        fromlines = open(fromfile, 'r').readlines()  # U?
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


class OutputFileNotFound(Exception):
    pass


