import os
import logging
import grpc
import service
import service.service_spec.style_transfer_pb2_grpc as grpc_bt_grpc
from service.service_spec.style_transfer_pb2 import Image
import subprocess
import concurrent.futures as futures
import sys
from PIL import Image as PIL_Image

logging.basicConfig(
    level=10, format="%(asctime)s - [%(levelname)8s] - %(name)s - %(message)s"
)
log = logging.getLogger("style_transfer_service")


class StyleTransferServicer(grpc_bt_grpc.StyleTransferServicer):
    """Style transfer servicer class to be added to the gRPC stub.
    Derived from protobuf (auto-generated) class."""

    def __init__(self):
        log.debug("StyleTransferServicer created!")
        self.result = "Fail"
        self.required_arguments = ['content', 'style']
        self.temp_dir = os.getcwd() + "/service/original-lua-code/temp/"
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
        self.saveExt = 'jpg'
        self.output_image_prefix = "outputimage_"
        # Store the names of the images to delete them afterwards
        self.created_images = []

    def treat_inputs(self, base_command, request, arguments):
        """Treats gRPC inputs and assembles lua command. Specifically, checks if required field have been specified,
        if the values and types are correct and, for each input/input_type adds the argument to the lua command."""

        # Base command is the prefix of the command (e.g.: 'th test.lua ')
        command = base_command
        for field, values in arguments.items():
            var_type = values[0]
            # required = values[1] Not being used now but required for future automation steps
            default = values[2]

            # Tries to retrieve argument from gRPC request
            try:
                arg_value = eval("request.{}".format(field))
            except Exception as e:  # AttributeError if trying to access a field that hasn't been specified.
                log.error(e)
                return False

            # Deals with each field (or field type) separately. This is very specific to the lua command required.
            # If fields
            if field == "content":
                assert(request.content != ""), "Content image path should not be empty."
                image_path, content_file_index_str = service.treat_image_input(arg_value, self.temp_dir, "{}".format(field))
                self.created_images.append(image_path)
                command += "-{} {} ".format(field, image_path)
            elif field == "style":
                assert (request.content != ""), "Style image path should not be empty."
                image_path, style_file_index_str = service.treat_image_input(arg_value, self.temp_dir, "{}".format(field))
                self.created_images.append(image_path)
                command += "-{} {} ".format(field, image_path)
            elif field == "alpha":
                if arg_value == 0.0:
                    continue
                else:
                    try:
                        float_alpha = float(arg_value)
                    except Exception as e:
                        log.error(e)
                        return False
                    if float_alpha < 0.0 or float_alpha > 1.0:
                        log.error("Argument alpha should be a real number between 0 and 1.")
                        return False
                    command += "-{} {} ".format(field, str(round(float_alpha, 2)))
            elif field == "saveExt":
                arg_value = arg_value.lower()
                if arg_value == "":
                    command += "-{} {} ".format(field, default)
                else:
                    if (arg_value == "jpg") or (arg_value == "png"):
                        command += "-{} {} ".format(field, arg_value)
                    else:
                        log.error("Field saveExt should either be jpg or png. Provided: {}.".format(arg_value))
                        return False
            else:
                # If types
                if var_type == "bool":
                    if eval("request.{}".format(field)):
                        command += "-{} ".format(field)
                elif var_type == "int":
                    try:
                        int(eval("request.{}".format(field)))
                    except Exception as e:
                        log.error(e)
                    command += "-{} {} ".format(field, eval("request.{}".format(field)))
        return command, content_file_index_str, style_file_index_str

    def _exit_handler(self):
        log.debug('Deleting temporary images before exiting.')
        for image in self.created_images:
            service.clear_file(image)

    def transfer_image_style(self, request, context):
        """Python wrapper to AdaIN Style Transfer written in lua.
        Receives gRPC request, treats the inputs and creates a thread that executes the lua command."""

        # Lua command call arguments. Key = argument name, value = tuple(type, required?, default_value)
        arguments = {"content": ("image", True, None),
                     "style": ("image", True, None),
                     # "mask": ("image", False, None), Not supported yet, will add once dApp and gRPC work
                     "contentSize": ("int", False, 0),
                     "styleSize": ("int", False, 0),
                     "preserveColor": ("bool", False, None),
                     "alpha": ("double", False, None),
                     # "styleInterpWeights": ???, Not supported yet, will add once dApp and gRPC work
                     "crop": ("bool", False, None),
                     "saveExt": ("string", False, "jpg")}

        # Treat inputs and assemble lua commands
        base_command = "th ./service/original-lua-code/test.lua "
        command, content_file_index_str, style_file_index_str = self.treat_inputs(base_command, request, arguments)
        command += "-{} {}".format("outputDir", self.temp_dir)  # pre-defined for the service

        log.debug("Lua command generated: {}".format(command))





        # Initializing parameters to reduce image size if necessary
        content_image_path = "contentimage_" + content_file_index_str + self.saveExt
        style_image_path = "styleimage_" + style_file_index_str + self.saveExt
        # Get output file path
        output_image_path = self.temp_dir + "contentimage_" + content_file_index_str \
            + "_stylized_styleimage_" + style_file_index_str + "." + self.saveExt
        starting_quality = 95
        current_quality = starting_quality
        reduce_quality_to = 9 / 10  # of input quality
        reduce_size_to = 4 / 5  # of input size
        number_of_attempts = 10
        resize_output_to_original = False

        # Retrieving original image size
        content_image = PIL_Image.open(content_image_path)
        original_content_size = content_image.size
        content_image.close()

        for resize_attempts in range(1, number_of_attempts + 1):
            # Call style transfer (Lua)
            process = subprocess.Popen(command.split(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess_output, subprocess_error = process.communicate()

            log.debug("Lua subprocess output: {}".format(subprocess_output))
            log.debug("Lua subprocess error: {}".format(subprocess_error))
            try:
                output_image = PIL_Image.open(output_image_path)
                self.created_images.append(output_image_path)
                if resize_output_to_original:
                    output_image = output_image.resize(original_content_size, PIL_Image.ANTIALIAS)
                    output_image.save(output_image_path, quality=starting_quality)
                break  # return output
            except Exception as e:  # TODO: how to identify?
                log.error(str(e))
                process.kill()

                # Opening content and style images and resizing them according to the number of attempts
                content_image = PIL_Image.open(content_image_path)
                style_image = PIL_Image.open(style_image_path)
                current_content_size = content_image.size
                current_style_size = style_image.size
                new_content_size = tuple(round(dim * reduce_size_to) for dim in current_content_size)
                new_style_size = tuple(round(dim * reduce_size_to) for dim in current_style_size)
                content_image = content_image.resize(new_content_size, PIL_Image.ANTIALIAS)
                style_image = style_image.resize(new_style_size, PIL_Image.ANTIALIAS)
                current_quality = round(current_quality * reduce_quality_to)
                content_image.save(content_image_path, quality=current_quality)
                style_image.save(style_image_path, quality=current_quality)
                content_image.close()
                style_image.close()
                log.info("Could not process image in current size. Reducing content image size from " + str(
                    current_content_size) + " to " + str(new_content_size) + ", style image size from " + str(
                    current_style_size) + " to " + str(
                    new_style_size) + " and trying again. Also reducing JPG quality by " + str(
                    round(1 - reduce_quality_to, 2)) + ".")

        if "out of memory".encode() in subprocess_error:
            for image in self.created_images:
                service.clear_file(image)
            error = subprocess_error.split(b"\n")[1]
            log.error(error)
            raise Exception(error)

        # Prepare gRPC output message
        self.result = Image()
        self.result.data = service.jpg_to_base64(output_image_path, open_file=True).decode("utf-8")
        log.debug("Output image generated. Service successfully completed.")

        for image in self.created_images:
            service.clear_file(image)

        return self.result


def serve(max_workers=5, port=7777):
    """The gRPC serve function.

    Params:
    max_workers: pool of threads to execute calls asynchronously
    port: gRPC server port

    Add all your classes to the server here.
    (from generated .py files by protobuf compiler)"""

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    grpc_bt_grpc.add_StyleTransferServicer_to_server(
        StyleTransferServicer(), server)
    server.add_insecure_port('[::]:{}'.format(port))
    return server


if __name__ == '__main__':
    """Runs the gRPC server to communicate with the Snet Daemon."""
    parser = service.common_parser(__file__)
    args = parser.parse_args(sys.argv[1:])
    service.main_loop(serve, args)
