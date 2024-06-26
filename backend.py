'''
REST API for semantic segmentation inference
'''

from flask import Response
from flask import Flask, request, Response, jsonify
from flask_cors import CORS

import base64
from PIL import Image
import numpy as np
import io
from Utils import InferenceWorker
import os
import cv2
import argparse

app = Flask(__name__)
CORS(app)

video_feed_sessions = {}

# Get the model weights path from the command line arguments
parser = argparse.ArgumentParser(description='Semantic Segmentation API')
parser.add_argument('--model_weights_path', type=str, default=None,
                    help='Path to the model weights file')
args = parser.parse_args()

MODEL_WEIGHTS_PATH = args.model_weights_path
assert MODEL_WEIGHTS_PATH is not None, 'Please provide the path to the model weights file'
inferenceWorker = InferenceWorker(model_weights_path=MODEL_WEIGHTS_PATH)


@app.route("/api/segment", methods=["POST"])
def segment():
    global video_feed_sessions

    # get the session_id generated from frontend
    session_id = request.args.get('session_id')
    session_tmp_dir = os.path.join('temp', session_id)
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    # Check if the file is an image or a video.
    content_type = request.files['file'].content_type
    if content_type.startswith('image/'):
        print(f'segmenting single image {file.filename} with session_id {session_id}')
        video_feed_sessions[session_id] = {'path': None, 'active': False}
        return segmentImage(file)
    elif content_type.startswith('video/'):
        temp_video_path = os.path.join(session_tmp_dir, file.filename)
        ensure_dir(temp_video_path)
        file.save(temp_video_path)
        video_feed_sessions[session_id] = {'path': temp_video_path, 'active': True}
        return segmentVideo(session_id)
    else:
        return jsonify({'error': 'Unsupported file type'}), 400


@app.route("/api/clear", methods=["POST"])
def clear():
    global video_feed_sessions

    # Get the session_id generated from frontend
    session_id = request.args.get('session_id')

    # clear the video feed session
    video_feed_sessions[session_id]['active'] = False

    # Clear the video file if it exists
    session_tmp_dir = os.path.join('temp', session_id)
    if os.path.exists(session_tmp_dir):
        if video_feed_sessions[session_id]['path'] is not None:
            os.remove(video_feed_sessions[session_id]['path'])
        os.removedirs(session_tmp_dir)

    return jsonify({'message': 'Temp directory cleared'}), 200


def segmentImage(file):
    '''segmenting a single image won't require the image file to be saved into disk'''
    # # Process the image
    image = Image.open(io.BytesIO(file.read()))
    image_np = np.array(image)
    image_np = image_np.astype(np.float32) / 255.0

    mask_pred, _ = inferenceWorker.segment(image_np)

    # Convert the mask to a base64 string
    mask_pred = (mask_pred * 255).astype('uint8')
    mask_image = Image.fromarray(mask_pred)

    # Convert the mask image to a base64 string
    buffered = io.BytesIO()
    mask_image.save(buffered, format="PNG")
    encoded_mask_image = base64.b64encode(buffered.getvalue()).decode()

    # Send the mask image back to the client
    maskPredB64str = f"data:image/png;base64,{encoded_mask_image}"
    return jsonify({'mask': maskPredB64str}), 200


def segmentVideo(session_id):
    global video_feed_sessions, inferenceWorker

    current_video_path = video_feed_sessions[session_id]['path']
    cap = cv2.VideoCapture(current_video_path)

    while cap.isOpened():

        # Stop the video feed if user clicks the clear button
        if not video_feed_sessions[session_id]['active']:
            break

        ret, frame = cap.read()
        if not ret:
            break

        # Convert the frame to RGB.
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Normalize the frame to [0, 1].
        frame_0_1 = frame_rgb.astype(np.float32) / 255.0

        # Segment the frame.
        segmented_frame, input_img_frame = inferenceWorker.segment(frame_0_1)

        # Scale them back up to [0, 255] and convert to uint8.
        segmented_frame_0_255 = (segmented_frame * 255).astype(np.uint8)
        input_img_frame_0_255 = (input_img_frame * 255).astype(np.uint8)

        # Create a 50-pixel wide white separator
        height = input_img_frame.shape[0]
        white_separator = np.full((height, 50, 3), 255, dtype=np.uint8)

        # Concatenate original frame, white separator, and segmented frame horizontally
        combined_frame = np.hstack((input_img_frame_0_255, white_separator, segmented_frame_0_255))

        ret, buffer = cv2.imencode('.jpg', combined_frame)
        frame_bytes = buffer.tobytes()

        # Yield the frame bytes
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

    cap.release()

@app.route('/video_feed')
def video_feed():
    global video_feed_sessions

    session_id = request.args.get('session_id')
    # if session_id not in video_feed_sessions or not video_feed_sessions[session_id]['active']:
    #     return jsonify({'message': 'Video cleared or session not found'}), 404
    return Response(segmentVideo(session_id),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

def ensure_dir(file_path):
    directory = os.path.dirname(file_path)
    if not os.path.exists(directory):
        os.makedirs(directory)


if __name__ == '__main__':
    app.run(debug=True)
