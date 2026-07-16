import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import os
import cv2
from time import monotonic, sleep

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

        # Reopen backoff state. Bugfix: a permanently dead device (unplugged,
        # or failed hard) used to cost a full 4-port rescan every ~30 s
        # forever, each one blocking the single-threaded executor on four
        # blocking read() calls and flooding the log. Back off instead, and
        # stop rescanning once it's clearly not coming back — the node stays
        # alive and publishes nothing, which is honest; a wrong frame or a
        # wedged executor is not.
        self._fail_streak = 0
        self._reopen_attempts = 0
        self._next_reopen_ts = 0.0
        self._gave_up = False

        # Initialize the camera
        self.init_camera()

        # Camera Frequency
        capture_freq = 1.0 / 30.0  # 30 frames a second
        self.camera_timer_ = self.create_timer(capture_freq, self.capture_image)

        self.get_logger().info('Standard Camera Node initialized...')

    # Backoff schedule for reopen attempts, in seconds. Escalates so a transient
    # bus glitch still recovers in seconds while a dead device settles to one
    # retry a minute instead of one every 30 s.
    REOPEN_BACKOFF_S = (0.0, 5.0, 15.0, 30.0, 60.0)
    REOPEN_GIVE_UP_AFTER = 10

    def _try_reopen(self):
        '''
        Reopen a stalled camera, with backoff and a give-up state.
        '''
        if self._gave_up:
            return
        now = monotonic()
        if now < self._next_reopen_ts:
            return  # still in the backoff window; don't rescan yet

        self._reopen_attempts += 1
        if self._reopen_attempts > self.REOPEN_GIVE_UP_AFTER:
            # Log ONCE and stop. Repeating this line every 30 s is what buried
            # the real errors in the log.
            self._gave_up = True
            self.get_logger().error(
                f'Camera did not recover after {self.REOPEN_GIVE_UP_AFTER} '
                'reopen attempts — giving up. No frames will be published. '
                'Reconnect the device and restart this node.')
            return

        idx = min(self._reopen_attempts - 1, len(self.REOPEN_BACKOFF_S) - 1)
        self.get_logger().warn(
            f'Stream stalled — reopening camera '
            f'(attempt {self._reopen_attempts}/{self.REOPEN_GIVE_UP_AFTER})...')
        self._release_camera()
        self.init_camera()
        self._fail_streak = 0
        self._next_reopen_ts = monotonic() + self.REOPEN_BACKOFF_S[idx]

    def _release_camera(self):
        '''
        Release the capture device if we have one. Safe to call any time.
        '''
        cam = getattr(self, 'camera', None)
        if cam is None:
            return
        try:
            cam.release()
        except Exception as exc:
            self.get_logger().warn(f'Camera release failed: {exc}')

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
        if self._gave_up:
            return
        if self.camera is None:
            # init_camera leaves this None only if opening raised. Treat it as
            # a stall rather than an AttributeError 30 times a second.
            self._fail_streak += 1
            self._try_reopen()
            return

        ret, image = self.camera.read()

        if not ret:
            # Bugfix: a USB glitch (e.g. the Farmduino resetting on the shared
            # bus when uart_controller opens the serial port) stalls the V4L2
            # stream, and a stalled capture NEVER recovers without a reopen —
            # the node used to log this error forever. After a few consecutive
            # failures, release and rescan so a bus glitch costs seconds, not
            # the session. read() blocks ~10 s on a stalled stream, so 3
            # failures ≈ 30 s of grace before the reopen.
            self._fail_streak += 1
            self.get_logger().error(
                f'Problem getting image ({self._fail_streak} consecutive).')
            if self._fail_streak >= 3:
                self._try_reopen()
            return

        # A good frame means the device is back: clear the backoff so a later,
        # unrelated glitch gets the full retry budget again.
        if self._reopen_attempts:
            self.get_logger().info(
                f'Camera recovered after {self._reopen_attempts} reopen '
                'attempt(s).')
            self._reopen_attempts = 0
            self._next_reopen_ts = 0.0
        self._fail_streak = 0
        image_msg = self.bridge_.cv2_to_imgmsg(image, "bgr8")
        self.rgb_publisher_.publish(image_msg)

    def destroy_node(self):
        '''
        Cleanup resources when shutting down the node
        '''
        # Bugfix: this used to be a bare self.camera.release(). init_camera
        # sets self.camera = None before scanning, so a failure in that window
        # left it None and shutdown raised AttributeError — which also skipped
        # super().destroy_node(), so the node was never torn down. Cancel the
        # timer first so no capture can fire mid-teardown.
        timer = getattr(self, 'camera_timer_', None)
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        self._release_camera()
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
