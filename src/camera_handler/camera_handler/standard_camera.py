import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import os
import cv2
from time import sleep

CAMERA = 'USB'  # Set your camera type here

class StandardCameraNode(Node):
    '''
    Camera Node that reads packets from the standard farmbot camera 
    and publishes the RGB frames on the topic
    '''
    def __init__(self):
        super().__init__('StandardCamera')
        self.bridge_ = CvBridge()  # Bridge to convert between ROS and OpenCV images

        # Initialize publishers for RGB and depth images
        self.rgb_publisher_ = self.create_publisher(Image, 'rgb_img', 10)

        # Initialize the camera
        self.init_camera()

        # Camera Frequency
        capture_freq = 1.0 / 30.0  # 30 frames a second
        self.camera_timer_ = self.create_timer(capture_freq, self.capture_image)

        self.get_logger().info('Standard Camera Node initialized...')

    def init_camera(self):
        '''
        Initialize the camera
        '''
        self.WIDTH = 640
        self.HEIGHT = 480
        self.discard_frames = 20  # Reduced number of discarded frames

        # Bugfix: the port was hardcoded to 0, but a USB hiccup re-enumerates
        # the camera onto /dev/video1 (observed on gh1, 2026-07-06) and the
        # node then errors forever. Scan the first few indices and keep the
        # first device that both opens AND delivers a frame (the Pi exposes
        # internal codec devices that open but never produce frames).
        self.camera = None
        self.camera_port = None
        for port in range(4):
            cam = cv2.VideoCapture(port)
            if cam.isOpened() and cam.read()[0]:
                self.camera = cam
                self.camera_port = port
                self.get_logger().info(f'Camera found on /dev/video{port}')
                break
            cam.release()
        if self.camera is None:
            self.camera = cv2.VideoCapture(0)  # keep old behaviour as fallback
            self.camera_port = 0
            self.get_logger().error('Error: Could not open video device.')
            return

        sleep(0.1)
        try:
            self.camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.WIDTH)
            self.camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.HEIGHT)
            self.camera.set(cv2.CAP_PROP_FPS, 30)  # Set camera frame rate
        except AttributeError:
            self.camera.set(cv2.cv.CV_CAP_PROP_FRAME_WIDTH, self.WIDTH)
            self.camera.set(cv2.cv.CV_CAP_PROP_FRAME_HEIGHT, self.HEIGHT)

        for _ in range(self.discard_frames):
            self.camera.grab()

    def capture_image(self):
        '''
        Take a photo using the farmbot snake camera and publish it to /rgb_img
        '''
        ret, image = self.camera.read()

        if not ret:
            # Bugfix: a USB glitch (e.g. the Farmduino resetting on the shared
            # bus when uart_controller opens the serial port) stalls the V4L2
            # stream, and a stalled capture NEVER recovers without a reopen —
            # the node used to log this error forever. After a few consecutive
            # failures, release and rescan so a bus glitch costs seconds, not
            # the session. read() blocks ~10 s on a stalled stream, so 3
            # failures ≈ 30 s of grace before the reopen.
            self._fail_streak = getattr(self, '_fail_streak', 0) + 1
            self.get_logger().error(
                f'Problem getting image ({self._fail_streak} consecutive).')
            if self._fail_streak >= 3:
                self.get_logger().warn('Stream stalled — reopening camera...')
                try:
                    self.camera.release()
                except Exception:
                    pass
                self.init_camera()
                self._fail_streak = 0
            return

        self._fail_streak = 0
        image_msg = self.bridge_.cv2_to_imgmsg(image, "bgr8")
        self.rgb_publisher_.publish(image_msg)

    def destroy_node(self):
        '''
        Cleanup resources when shutting down the node
        '''
        self.camera.release()
        self.get_logger().info('Camera released.')
        super().destroy_node()

# Main Function called on the initialization of the ROS2 Node
def main(args = None):
    rclpy.init(args = args)

    cam = StandardCameraNode()

    try:
        rclpy.spin(cam)
    except KeyboardInterrupt:
        pass

    cam.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
