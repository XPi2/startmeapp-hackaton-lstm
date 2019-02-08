# -*- coding: utf-8 -*-
"""
Class definition of YOLO_v3 style detection model on image and video
"""

import colorsys
import os
from matplotlib import colors
import requests
import cv2
import json
import base64

import numpy as np
from PIL import Image, ImageFont, ImageDraw
from timeit import default_timer as timer
from keras import backend as K
from keras.utils import multi_gpu_model
from keras.models import load_model
from keras.layers import Input
from yolo3.model import yolo_eval, yolo_body, tiny_yolo_body
from yolo3.utils import letterbox_image
from keras.utils import multi_gpu_model
from io import BytesIO

class YOLO(object):

    _defaults = {
        # Change to your weights file
        #"model_path": 'model_data/coco_weights.h5',
        # Change to your kmeans.py generated anchors file
        #"anchors_path": 'model_data/coco_anchors.txt',
        # Change to your classes file
        #"classes_path": 'model_data/coco_classes.txt',
        "score": 0.3,
        "iou": 0.45,
        #"colors": 'r, w, b',
        # Check your model image size
        "model_image_size": (416, 416),
        #"gpu_num": 1,
    }

    @classmethod
    def get_defaults(cls, n):
        if n in cls._defaults:
            return cls._defaults[n]
        else:
            return "Unrecognized attribute name '" + n + "'"

    def __init__(self, **kwargs):
        print("kwargs", kwargs)
        self.__dict__.update(self._defaults) # set up default values
        self.__dict__.update(kwargs) # and update with user overrides
        print("\nselfDict", self.__dict__)
        self.class_names = self._get_class()
        self.anchors = self._get_anchors()
        self.sess = K.get_session()
        self.forceColors = self.colors
        self.boxes, self.scores, self.classes = self.generate()
        print("\nUsing %s | %s | %s" % (self.model_path,
              self.anchors_path, self.classes_path))

    def _get_class(self):
        classes_path = os.path.expanduser(self.classes_path)
        with open(classes_path) as f:
            class_names = f.readlines()
        class_names = [c.strip() for c in class_names]
        return class_names

    def _get_anchors(self):
        anchors_path = os.path.expanduser(self.anchors_path)
        with open(anchors_path) as f:
            anchors = f.readline()
        anchors = [float(x) for x in anchors.split(',')]
        return np.array(anchors).reshape(-1, 2)

    def generate(self):
        model_path = os.path.expanduser(self.model_path)
        assert model_path.endswith('.h5'), 'Keras model or weights must be a .h5 file.'

        # Load model, or construct model and load weights.
        num_anchors = len(self.anchors)
        num_classes = len(self.class_names)
        is_tiny_version = num_anchors==6 # default setting
        try:
            self.yolo_model = load_model(model_path, compile=False)
        except:
            self.yolo_model = tiny_yolo_body(Input(shape=(None,None,3)), num_anchors//2, num_classes) \
                if is_tiny_version else yolo_body(Input(shape=(None,None,3)), num_anchors//3, num_classes)
            self.yolo_model.load_weights(self.model_path) # make sure model, anchors and classes match
        else:
            assert self.yolo_model.layers[-1].output_shape[-1] == \
                num_anchors/len(self.yolo_model.output) * (num_classes + 5), \
                'Mismatch between model and given anchor and class sizes'

        print('{} model, anchors, and classes loaded.'.format(model_path))

        # Generate colors for drawing bounding boxes.
        if self.forceColors:
            forcedColors = list(self.forceColors.replace(" ", "").split(','))
            self.colors = list(map(lambda x: colors.to_rgb(*x), forcedColors))
            #self.colors = forcedColors # The color interpretation can be done by Pillow (ex: red)
        else:
            hsv_tuples = [(x / len(self.class_names), 1., 1.)
                          for x in range(len(self.class_names))]
            self.colors = list(map(lambda x: colorsys.hsv_to_rgb(*x), hsv_tuples))
            np.random.seed(10101)  # Fixed seed for consistent colors across runs.
            np.random.shuffle(self.colors)  # Shuffle colors to decorrelate adjacent classes.
            np.random.seed(None)  # Reset seed to default.
            #self.colors = list(
            #    map(lambda x: (int(x[2] * 255), int(x[1] * 255), int(x[0] * 255)),
            #        self.colors))
        
        self.colors = list(
                map(lambda x: (int(x[2] * 255), int(x[1] * 255), int(x[0] * 255)),
                    self.colors))

        # Generate output tensor targets for filtered bounding boxes.
        self.input_image_shape = K.placeholder(shape=(2, ))
        if self.gpu_num>=2:
            self.yolo_model = multi_gpu_model(self.yolo_model, gpus=self.gpu_num)
        boxes, scores, classes = yolo_eval(self.yolo_model.output, self.anchors,
                len(self.class_names), self.input_image_shape,
                score_threshold=self.score, iou_threshold=self.iou)
        return boxes, scores, classes

    def detect_image(self, image):
        start = timer()

        if self.model_image_size != (None, None):
            assert self.model_image_size[0]%32 == 0, 'Multiples of 32 required'
            assert self.model_image_size[1]%32 == 0, 'Multiples of 32 required'
            boxed_image = letterbox_image(image, tuple(reversed(self.model_image_size)))
        else:
            new_image_size = (image.width - (image.width % 32),
                              image.height - (image.height % 32))
            boxed_image = letterbox_image(image, new_image_size)
        image_data = np.array(boxed_image, dtype='float32')

        print(image_data.shape)
        image_data /= 255.
        image_data = np.expand_dims(image_data, 0)  # Add batch dimension.

        out_boxes, out_scores, out_classes = self.sess.run(
            [self.boxes, self.scores, self.classes],
            feed_dict={
                self.yolo_model.input: image_data,
                self.input_image_shape: [image.size[1], image.size[0]],
                K.learning_phase(): 0
            })


        font = ImageFont.truetype(font='Arial.ttf',
                    size=np.floor(3e-2 * image.size[1] + 0.5).astype('int32'))
        thickness = (image.size[0] + image.size[1]) // 300

        for i, c in reversed(list(enumerate(out_classes))):
            predicted_class = self.class_names[c]
            box = out_boxes[i]
            score = out_scores[i]
            if self.noscore:
                label = '{}'.format(predicted_class)
            else:
                label = '{} {:.2f}'.format(predicted_class, score)
            draw = ImageDraw.Draw(image)
            label_size = draw.textsize(label, font)
            top, left, bottom, right = box
            top = max(0, np.floor(top + 0.5).astype('int32'))
            left = max(0, np.floor(left + 0.5).astype('int32'))
            bottom = min(image.size[1], np.floor(bottom + 0.5).astype('int32'))
            right = min(image.size[0], np.floor(right + 0.5).astype('int32'))
            print(label, (left, top), (right, bottom))

            object_data = {'id': str(i)}
            object_data['class_name'] = predicted_class
            object_data['box'] = box.tolist()

            if top - label_size[1] >= 0:
                text_origin = np.array([left, top - label_size[1]])
            else:
                text_origin = np.array([left, top + 1])

            # My kingdom for a good redistributable image drawing library.
            for i in range(thickness):
                draw.rectangle(
                    [left + i, top + i, right - i, bottom - i],
                    outline=self.colors[c])
            draw.rectangle(
                [tuple(text_origin), tuple(text_origin + label_size)],
                fill=self.colors[c])
            if self.colors[c] == (255, 255, 255):
                draw.text(text_origin, label, fill=(0,0,0), font=font)
            else:
                draw.text(text_origin, label, fill=(255,255,255), font=font)
            del draw

        buffered = BytesIO()
        image_post = Image.fromarray(cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR))
        image_post.save(buffered, format="JPEG")

        image_post = str(base64.b64encode(buffered.getvalue()), 'UTF-8')
        # print(encode_img)

        print('Found {} boxes for {}'.format(len(out_boxes), 'img'))
        # David function POST
        data = {
            "num_objects": len(out_boxes),
            "objects": "patera",
            "frame": image_post 
        }
        try:
<<<<<<< HEAD
            requests.post('http://localhost:5000/post/8', json=data, timeout=0.00000001)
        except:
=======
            requests.post('http://localhost:5000/post/8', json=data, timeout=0.0000000001)
        except requests.exceptions.ReadTimeout: 
>>>>>>> 24be8b999792f0b323216219dc6da8c1cd5599ce
            pass
        end = timer()
        print(end - start)
        return image

    def close_session(self):
        self.sess.close()

def detect_video(yolo, webcam, video_path, output_path):
    if webcam:
        vid = cv2.VideoCapture(0)
        output_path = ""
    else:
        vid = cv2.VideoCapture(video_path)
        #To avoid output_path
        output_path = ""
    if not vid.isOpened():
        raise IOError("Couldn't open webcam or video")
    video_FourCC    = int(vid.get(cv2.CAP_PROP_FOURCC))
    video_fps       = vid.get(cv2.CAP_PROP_FPS)
    video_size      = (int(vid.get(cv2.CAP_PROP_FRAME_WIDTH)),
                        int(vid.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    isOutput = True if output_path != "" else False
    if isOutput:
        # TODO: create folder if it doesn't exist + check extension avi or mp4 or mkv to know if its a dir or not
        print("!!! TYPE:", type(output_path), type(video_FourCC), type(video_fps), type(video_size))
        out = cv2.VideoWriter(output_path, video_FourCC, video_fps, video_size)
    accum_time = 0
    curr_fps = 0
    fps = "FPS: ??"
    prev_time = timer()
    while True:
        return_value, frame = vid.read()
        image = Image.fromarray(frame)
        image = yolo.detect_image(image)
        result = np.asarray(image)
        curr_time = timer()
        exec_time = curr_time - prev_time
        prev_time = curr_time
        accum_time = accum_time + exec_time
        curr_fps = curr_fps + 1
        if accum_time > 1:
            accum_time = accum_time - 1
            fps = "FPS: " + str(curr_fps)
            curr_fps = 0
        #cv2.putText(result, text=fps, org=(3, 15), fontFace=cv2.FONT_HERSHEY_SIMPLEX,
        #            fontScale=0.50, color=(255, 0, 0), thickness=2)
        #cv2.namedWindow("result", cv2.WINDOW_NORMAL)
        #cv2.imshow("result", result)
        if isOutput:
            out.write(result)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break 
    yolo.close_session()

