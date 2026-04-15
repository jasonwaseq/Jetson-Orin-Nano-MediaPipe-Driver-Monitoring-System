"""
Jetson Video Recorder (with Live Display)
Saves a raw MP4 from the camera.
Usage: 
  python3 record_video.py --name my_test --duration 10
"""
import cv2
import time
import argparse
import os

def record():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", type=str, default="test_capture", help="Name of the file")
    parser.add_argument("--duration", type=int, default=10, help="Seconds to record")
    parser.add_argument("--source", type=int, default=0, help="Camera index")
    parser.add_argument("--no-display", action="store_true", help="Disable live display window")
    args = parser.parse_args()

    # Determine save path (handles being run from root or recordings/ folder)
    if os.path.basename(os.getcwd()) == "recordings":
        save_dir = "."
    else:
        save_dir = "recordings"
        os.makedirs(save_dir, exist_ok=True)
    
    filename = os.path.join(save_dir, f"{args.name}_{int(time.time())}.mp4")

    # Try to open camera
    cap = cv2.VideoCapture(args.source)
    if not cap.isOpened():
        print(f"Error: Could not open camera {args.source}")
        return

    # Get camera properties
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps    = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0: fps = 30 # Fallback

    # Setup Writer (H.264 / MP4)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(filename, fourcc, fps, (width, height))

    print(f"Recording {args.duration}s to: {filename}")
    print("Commands: 'q' to quit early.")

    start_time = time.time()
    frames = 0
    
    try:
        while (time.time() - start_time) < args.duration:
            ret, frame = cap.read()
            if not ret: break
            
            # Save frame to disk
            out.write(frame)
            frames += 1
            
            # Live Display (if not disabled)
            if not args.no_display:
                cv2.imshow("Recording...", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("\nStopped by keyboard.")
                    break
            
            # Show progress on console
            if frames % 30 == 0:
                print(f"Recorded {int(time.time() - start_time)}s...", end="\r")

        print(f"\nFinished! Recorded {frames} frames.")
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        cap.release()
        out.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    record()
