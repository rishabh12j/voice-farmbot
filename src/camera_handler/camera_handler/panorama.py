import cv2
import numpy as np
import os
import yaml
from ament_index_python.packages import get_package_share_directory
from cv_bridge import CvBridge
from sensor_msgs.msg import Image
import math
from rclpy.node import Node

# Panorama map resolution (mm per MAP pixel). The camera's coord_scale can be
# very fine (sub-mm/px), which would make a full-bed canvas gigapixel-sized and
# impossible to write. The map is rendered at this coarser resolution and each
# camera frame is downscaled to match. Lower = sharper map but bigger/slower.
MAP_MM_PER_PX = 2.0


class Panorama:
    '''
    Class used for taking pictures from the farmbot and stitching them into
    a panorama representing the map information
    '''
    def __init__(self, node: Node):
        '''
        Panorama module constructor extending a node's functionality
        '''
        self.node_ = node
        
        self.map_x = -1.0
        self.map_y = -1.0
        self.config_directory_ = os.path.join(get_package_share_directory('camera_handler'), 'config')
        self.calib_file_ = 'camera_calibration.yaml'
        
        self.bridge = CvBridge()
        self.rgb_image_ = None
        self.depth_image_ = None
        self.rgb_sub = self.node_.create_subscription(Image, '/rgb_img', self.__rgb_callback, 10)
        self.depth_sub = self.node_.create_subscription(Image, '/depth_img', self.__depth_callback, 10)

    def __rgb_callback(self, msg):
        '''
        Subscriber callback for the RGB camera feed
        '''
        self.rgb_image_ = self.bridge.imgmsg_to_cv2(msg, "bgr8")

    def __depth_callback(self, msg):
        '''
        Subscriber callback for the Depth camera feed
        '''
        self.depth_image_ = self.bridge.imgmsg_to_cv2(msg, "mono8")
        
    def initialize_map_if_needed(self, map_path):
        '''
        If the map does not exist at the specified path, create a blank canvas and write it
        '''
        if not os.path.exists(map_path):
            # Create a blank canvas with 4 channels for RGBA
            blank_canvas = np.zeros((self.map_size_y_px, self.map_size_x_px, 4), dtype=np.uint8)
            cv2.imwrite(map_path, blank_canvas)

    def load_from_yaml(self, path: str, file_name: str):
        '''
        Loads the specified yaml file from the specified path and returns a 
        the dictionary within the file
        '''
        # Load configuration data from a YAML file
        full_path = os.path.join(path, file_name)
        if not os.path.exists(full_path):
            self.node_.get_logger().warn(f"File path is invalid: {full_path}")
            return None
        
        with open(full_path, 'r') as yaml_file:
            try:
                return yaml.safe_load(yaml_file)
            except yaml.YAMLError as e:
                self.node_.get_logger().warn(f"Error reading YAML file: {e}")
                return None
    
    def save_image_for_mosaic(self, num: int):
        mosaic_directory = os.path.join(self.config_directory_, 'mosaic')
        filename = f"{mosaic_directory}/image_{num}.png"
        os.makedirs(mosaic_directory, exist_ok=True)
        cv2.imwrite(filename, self.rgb_image_)
        self.node_.get_logger().info(f'saved mosaic image {num:03}')
        
    def stitch_image_onto_map(self, x: float, y: float):
        '''
        Takes a picture from the Luxonis camera and stitches it to the 
        panorama map at the specified x and y coordinates
        '''
        # Loading the camera calibration information
        self.config_data = self.load_from_yaml(self.config_directory_, self.calib_file_)
        if self.config_data is None:
            # No camera_calibration.yaml yet — don't dereference None (which would
            # kill camera_controller). Tell the user to calibrate first and bail.
            self.node_.get_logger().warn(
                'No camera calibration found (run I_0 first). Skipping stitch.')
            return

        # If the map dimensions were not set at the start of the run, load them from the active map
        if self.map_x == -1.0 or self.map_y == -1.0:
            # Load map instance
            # Active map now lives in the shared writable state dir (Option 2),
            # not the package share — read it from there so we see the live map.
            map_directory_ = os.environ.get('FARMBOT_STATE_DIR') or os.path.join(os.path.expanduser('~'), '.farmbot')
            map_file = 'active_map.yaml'
            map_instance = self.load_from_yaml(map_directory_, map_file)
            if map_instance:
                # Get map dimensions
                self.map_x = map_instance['map_reference']['x_len']
                self.map_y = map_instance['map_reference']['y_len']

                self.node_.get_logger().info('Loading map dimensions from active map file')
        
        # Map canvas size + placement use the (coarse) map resolution, not the
        # camera's fine coord_scale, so the full-bed canvas stays a sane size.
        self.map_size_x_px = int(self.map_x / MAP_MM_PER_PX)
        self.map_size_y_px = int(self.map_y / MAP_MM_PER_PX)

        x_px = int(x / MAP_MM_PER_PX)
        y_px = int(y / MAP_MM_PER_PX)
        
        if self.rgb_image_ is None:
            self.node_.get_logger().warn('RGB image is not available.')
            return

        # Rotate and add a transparent mask to the RGB image
        rotation_angle = self.config_data['total_rotation_angle']
        processed_image = self.rotate_and_mask_image(self.rgb_image_, rotation_angle)
        
        # Ensure processed_image has 4 channels
        if processed_image.shape[2] != 4:
            processed_image = cv2.cvtColor(processed_image, cv2.COLOR_BGR2BGRA)

        # Downscale the camera frame from its (fine) coord_scale to the (coarse)
        # map resolution so it pastes at the correct physical size on the map.
        map_scale = self.config_data['coord_scale'] / MAP_MM_PER_PX
        if map_scale < 0.999:
            processed_image = cv2.resize(processed_image, None, fx=map_scale,
                                         fy=map_scale, interpolation=cv2.INTER_AREA)

        # Get the dimensions of the processed image
        new_img_height, new_img_width = processed_image.shape[:2]
        
        if new_img_width == 0 or new_img_height == 0:
            self.node_.get_logger().warn('Processed image has zero width or height.')
            return
        
        # Load or initialize the map (panorama) image 
        map_path = os.path.join(self.config_directory_,'rgb_map.png')
        self.initialize_map_if_needed(map_path)
        map_image = cv2.imread(map_path, cv2.IMREAD_UNCHANGED)  # Ensures map_image is read with alpha channel

        if map_image is None:
            # File missing or unreadable (e.g. a stale gigapixel canvas left over
            # from before the downscale fix). Recreate a fresh blank canvas instead
            # of bailing — but guard against a genuinely huge (misconfigured) size.
            megapixels = (self.map_size_x_px * self.map_size_y_px) / 1e6
            if megapixels > 60:
                self.node_.get_logger().error(
                    'Refusing to allocate a %dx%d (%.0f MP) panorama canvas — check '
                    'MAP_MM_PER_PX. Skipping stitch.'
                    % (self.map_size_x_px, self.map_size_y_px, megapixels))
                return
            self.node_.get_logger().warn(
                'Map canvas missing/unreadable at %s — creating a fresh one (%dx%d).'
                % (map_path, self.map_size_x_px, self.map_size_y_px))
            map_image = np.zeros((self.map_size_y_px, self.map_size_x_px, 4),
                                 dtype=np.uint8)

        # Ensure map_image has 4 channels
        if map_image.shape[2] != 4:
            map_image = cv2.cvtColor(map_image, cv2.COLOR_BGR2BGRA)

        # Calculate placement and cropping
        map_height, map_width = map_image.shape[:2]
        
        # Calculate the top-left corner of the image on the map
        start_x = x_px - new_img_width // 2
        start_y = map_height - y_px - new_img_height // 2  # Note: map_height - y_px to flip the y-coordinate

        # Calculate the ROI in the map image and the corresponding region in the new image
        roi_start_x = max(start_x, 0)
        roi_start_y = max(start_y, 0)
        roi_end_x = min(start_x + new_img_width, map_width)
        roi_end_y = min(start_y + new_img_height, map_height)

        new_start_x = max(-start_x, 0)
        new_start_y = max(-start_y, 0)
        new_end_x = new_start_x + (roi_end_x - roi_start_x)
        new_end_y = new_start_y + (roi_end_y - roi_start_y)

        self.node_.get_logger().info(f'start_x: {start_x}, start_y: {start_y}, roi_start_x: {roi_start_x}, roi_start_y: {roi_start_y}, roi_end_x: {roi_end_x}, roi_end_y: {roi_end_y}')
        self.node_.get_logger().info(f'new_start_x: {new_start_x}, new_start_y: {new_start_y}, new_end_x: {new_end_x}, new_end_y: {new_end_y}')
        
        # Copy the overlapping area from new_image to map_image
        if roi_end_x > roi_start_x and roi_end_y > roi_start_y:
            alpha_s = processed_image[new_start_y:new_end_y, new_start_x:new_end_x, 3] / 255.0
            alpha_l = 1.0 - alpha_s

            for c in range(0, 3):
                map_image[roi_start_y:roi_end_y, roi_start_x:roi_end_x, c] = (alpha_s * processed_image[new_start_y:new_end_y, new_start_x:new_end_x, c] +
                                                                              alpha_l * map_image[roi_start_y:roi_end_y, roi_start_x:roi_end_x, c])

            map_image[roi_start_y:roi_end_y, roi_start_x:roi_end_x, 3] = (alpha_s * processed_image[new_start_y:new_end_y, new_start_x:new_end_x, 3] +
                                                                          alpha_l * map_image[roi_start_y:roi_end_y, roi_start_x:roi_end_x, 3])

        # Save the updated map image
        cv2.imwrite(map_path, map_image)
        self.node_.get_logger().info('Picture stitched to the panorama successfully.')

    def rotate_and_mask_image(self, image, angle):
        '''
        Rotates the image around the center at the set angle and adds a transparent mask.
        '''
        # Get the dimensions of the image
        height, width = image.shape[:2]

        # Calculate the rotation center
        center = (width // 2, height // 2)

        # Perform the rotation
        rotation_matrix = cv2.getRotationMatrix2D(center, -angle, 1.0)

        # Determine the new bounding dimensions of the rotated image
        abs_cos = abs(rotation_matrix[0, 0])
        abs_sin = abs(rotation_matrix[0, 1])

        bound_w = int(height * abs_sin + width * abs_cos)
        bound_h = int(height * abs_cos + width * abs_sin)

        # Adjust the rotation matrix to take into account translation
        rotation_matrix[0, 2] += bound_w / 2 - center[0]
        rotation_matrix[1, 2] += bound_h / 2 - center[1]

        # Perform the actual rotation
        rotated_image = cv2.warpAffine(image, rotation_matrix, (bound_w, bound_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))

        # Create an alpha channel with full opacity
        alpha_channel = np.ones((rotated_image.shape[0], rotated_image.shape[1], 1), dtype=rotated_image.dtype) * 255
        rotated_image_rgba = np.concatenate((rotated_image, alpha_channel), axis=2)

        # Mask the rotated image
        mask = np.zeros((bound_h, bound_w, 4), dtype=np.uint8)
        cv2.fillConvexPoly(mask, cv2.boxPoints(((width // 2, height // 2), (width, height), angle)).astype(int), (255, 255, 255, 255))

        masked_image = cv2.bitwise_and(rotated_image_rgba, mask)

        return masked_image
    
    def get_panorama_increments(self):
        '''
        Get the panorama increments for the x and y axis for minimizing 
        the amount of images needed to stitch the whole map into a panorama
        '''
        self.config_data_ = self.load_from_yaml(self.config_directory_, self.calib_file_)
        
        if self.map_x == -1.0 or self.map_y == -1.0:
            # Load map instance
            # Active map now lives in the shared writable state dir (Option 2),
            # not the package share — read it from there so we see the live map.
            map_directory_ = os.environ.get('FARMBOT_STATE_DIR') or os.path.join(os.path.expanduser('~'), '.farmbot')
            map_file = 'active_map.yaml'
            map_instance = self.load_from_yaml(map_directory_, map_file)
            if map_instance:
                # Get map dimensions
                self.map_x = map_instance['map_reference']['x_len']
                self.map_y = map_instance['map_reference']['y_len']

                self.node_.get_logger().info('Loading map dimensions from active map file')
        
        if self.rgb_image_ is None:
            self.node_.get_logger().warn('RGB image is not available.')
            return 0, 0

        # Rotate the RGB image and add a transparent mask
        rotation_angle = self.config_data_['total_rotation_angle']
        processed_image = self.rotate_and_mask_image(self.rgb_image_, rotation_angle)

        # Ensure processed_image has 4 channels
        if processed_image.shape[2] != 4:
            processed_image = cv2.cvtColor(processed_image, cv2.COLOR_BGR2BGRA)

        # Get the dimensions of the processed image
        height, width = processed_image.shape[:2] 

        if width == 0 or height == 0:
            self.node_.get_logger().warn('Processed image has zero width or height.')
            return 0, 0

        # Calculate the bounding box of the non-transparent area
        gray = cv2.cvtColor(processed_image, cv2.COLOR_BGRA2GRAY)
        _, thresh = cv2.threshold(gray, 1, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return 0, 0
        x, y, w, h = cv2.boundingRect(contours[0])

        # Convert the dimensions to millimeters using the coordinate scale
        height_mm = h * self.config_data_['coord_scale']
        width_mm = w * self.config_data_['coord_scale']

        return height_mm, width_mm  # x, y

