"""Optional CPU face tracking for crop position, with explicit no-face fallback."""
import importlib.util
import math


def available():
    return importlib.util.find_spec("cv2") is not None


def smooth_track(detections, width, height, aspect, fallback=.5):
    crop_width = min(width, height * aspect)
    if width <= crop_width:
        return []
    previous, track = None, []
    for time, faces in detections:
        if not faces:
            continue
        # Follow the same nearby face where possible; no speaker/identity claim.
        face = min(faces, key=lambda r: abs((r[0] + r[2] / 2) - previous)) if previous is not None else max(faces, key=lambda r: r[2] * r[3])
        center = face[0] + face[2] / 2
        previous = center if previous is None else previous * .65 + center * .35
        x = min(1, max(0, (previous - crop_width / 2) / (width - crop_width)))
        # Reduce needless movement and cap keyframes to one per sampled second.
        if not track or abs(x - track[-1]["x"]) > .025:
            track.append({"time": round(time, 3), "x": round(x, 4)})
    if not track:
        return []
    return [{"time": 0, "x": track[0]["x"]}, *[p for p in track if p["time"] > 0]]


def follow_faces(source, start, end, aspect):
    if not available():
        raise ValueError("Face following needs OpenCV. Run npm run tracking:install once; manual framing remains available.")
    import cv2
    from worker.visual import frame
    import numpy as np
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if detector.empty():
        raise ValueError("OpenCV face detector data is missing. Reinstall the tracking dependency.")
    detections, width, height = [], 0, 0
    # One 480-wide frame every second, restricted to the selected clip.
    for offset in range(math.ceil(end - start)):
        data = frame(source, start + offset, width=480)
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        height, width = image.shape
        boxes = detector.detectMultiScale(image, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        detections.append((offset, list(boxes)))
    points = smooth_track(detections, width, height, aspect)
    if not points:
        raise ValueError("No consistent frontal face was found. Use manual framing for this clip.")
    return points


def crop_expression(points, fallback):
    """Bounded numeric FFmpeg expression; positions interpolate between detections."""
    if not points:
        return str(fallback)
    # At most 30 keyframes keeps FFmpeg parser depth bounded for long clips.
    stride = max(1, math.ceil(len(points) / 29))
    reduced = points[::stride]
    if reduced[-1] != points[-1]:
        reduced.append(points[-1])
    expression = str(reduced[-1]["x"])
    for a, b in reversed(list(zip(reduced, reduced[1:]))):
        dt = max(.001, b["time"] - a["time"])
        part = f"({a['x']}+({b['x']}-{a['x']})*clip((t-{a['time']})/{dt},0,1))"
        expression = f"if(lt(t,{b['time']}),{part},{expression})"
    return expression
