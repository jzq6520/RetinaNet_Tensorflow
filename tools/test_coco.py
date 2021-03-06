# -*- coding:utf-8 -*-

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

import os, sys
import tensorflow as tf
import time
import cv2
import pickle
import numpy as np
import json
sys.path.append("../")

from data.io.image_preprocess import short_side_resize_for_inference_data
from libs.configs import cfgs
from libs.networks import build_whole_network_batch
from libs.val_libs import voc_eval
from libs.box_utils import draw_box_in_img
import argparse
from help_utils import tools
from libs.label_name_dict.label_dict import *

from data.lib_coco.PythonAPI.pycocotools.coco import COCO
from data.lib_coco.PythonAPI.pycocotools.cocoeval import COCOeval


def test_coco(det_net, real_test_img_list, eval_data, draw_imgs=False):

    # 1. preprocess img
    img_plac = tf.placeholder(dtype=tf.uint8, shape=[None, None, 3])  # is RGB. not BGR
    img_batch = tf.cast(img_plac, tf.float32)

    img_batch = short_side_resize_for_inference_data(img_tensor=img_batch,
                                                     target_shortside_len=cfgs.IMG_SHORT_SIDE_LEN,
                                                     length_limitation=cfgs.IMG_MAX_LENGTH)
    if cfgs.NET_NAME in ['resnet152_v1d', 'resnet101_v1d', 'resnet50_v1d']:
        img_batch = (img_batch / 255 - tf.constant(cfgs.PIXEL_MEAN_)) / tf.constant(cfgs.PIXEL_STD)
    else:
        img_batch = img_batch - tf.constant(cfgs.PIXEL_MEAN)

    # img_batch = (img_batch - tf.constant(cfgs.PIXEL_MEAN)) / (tf.constant(cfgs.PIXEL_STD)*255)
    img_batch = tf.expand_dims(img_batch, axis=0)

    detection_boxes, detection_scores, detection_category = det_net.build_whole_detection_network(
        input_img_batch=img_batch,
        gtboxes_batch=None)

    init_op = tf.group(
        tf.global_variables_initializer(),
        tf.local_variables_initializer()
    )

    restorer, restore_ckpt = det_net.get_restorer()

    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True

    with tf.Session(config=config) as sess:
        sess.run(init_op)
        if not restorer is None:
            restorer.restore(sess, restore_ckpt)
            print('restore model')

        save_path = os.path.join('./eval_coco', cfgs.VERSION)
        tools.mkdir(save_path)
        fw_json_dt = open(os.path.join(save_path, 'coco_test-dev.json'), 'w')
        coco_det = []
        for i, a_img in enumerate(real_test_img_list):

            raw_img = cv2.imread(os.path.join(eval_data, a_img['file_name']))
            raw_h, raw_w = raw_img.shape[0], raw_img.shape[1]

            start = time.time()
            resized_img, detected_boxes, detected_scores, detected_categories = \
                sess.run(
                    [img_batch, detection_boxes, detection_scores, detection_category],
                    feed_dict={img_plac: raw_img[:, :, ::-1]}  # cv is BGR. But need RGB
                )
            end = time.time()

            eval_indices = detected_scores >= 0.01
            detected_scores = detected_scores[eval_indices]
            detected_boxes = detected_boxes[eval_indices]
            detected_categories = detected_categories[eval_indices]

            # print("{} cost time : {} ".format(img_name, (end - start)))
            if draw_imgs:
                show_indices = detected_scores >= cfgs.VIS_SCORE
                show_scores = detected_scores[show_indices]
                show_boxes = detected_boxes[show_indices]
                show_categories = detected_categories[show_indices]

                draw_img = np.squeeze(resized_img, 0)
                if cfgs.NET_NAME in ['resnet152_v1d', 'resnet101_v1d', 'resnet50_v1d']:
                    draw_img = (draw_img * np.array(cfgs.PIXEL_STD) + np.array(cfgs.PIXEL_MEAN_)) * 255
                else:
                    draw_img = draw_img + np.array(cfgs.PIXEL_MEAN)

                # draw_img = draw_img * (np.array(cfgs.PIXEL_STD)*255) + np.array(cfgs.PIXEL_MEAN)

                final_detections = draw_box_in_img.draw_boxes_with_label_and_scores(draw_img,
                                                                                    boxes=show_boxes,
                                                                                    labels=show_categories,
                                                                                    scores=show_scores,
                                                                                    in_graph=False)
                if not os.path.exists(cfgs.TEST_SAVE_PATH):
                    os.makedirs(cfgs.TEST_SAVE_PATH)

                cv2.imwrite(cfgs.TEST_SAVE_PATH + '/' + '{}.jpg'.format(a_img['id']),
                            final_detections[:, :, ::-1])

            xmin, ymin, xmax, ymax = detected_boxes[:, 0], detected_boxes[:, 1], \
                                     detected_boxes[:, 2], detected_boxes[:, 3]

            resized_h, resized_w = resized_img.shape[1], resized_img.shape[2]

            xmin = xmin * raw_w / resized_w
            xmax = xmax * raw_w / resized_w

            ymin = ymin * raw_h / resized_h
            ymax = ymax * raw_h / resized_h

            boxes = np.transpose(np.stack([xmin, ymin, xmax-xmin, ymax-ymin]))

            # cost much time
            for j, box in enumerate(boxes):
                coco_det.append({'bbox': [float(box[0]), float(box[1]), float(box[2]), float(box[3])],
                                 'score': float(detected_scores[j]), 'image_id': a_img['id'],
                                 'category_id': int(classes_originID[LABEL_NAME_MAP[detected_categories[j]]])})

            tools.view_bar('%s image cost %.3fs' % (a_img['id'], (end - start)), i + 1, len(real_test_img_list))

        json.dump(coco_det, fw_json_dt)
        fw_json_dt.close()


def eval(num_imgs, eval_data, json_file, showbox):

    with open(json_file) as f:
        test_img_list = json.load(f)['images']

    if num_imgs == np.inf:
        real_test_img_list = test_img_list
    else:
        real_test_img_list = test_img_list[: num_imgs]

    faster_rcnn = build_whole_network_batch.DetectionNetwork(base_network_name=cfgs.NET_NAME,
                                                             is_training=False)
    test_coco(det_net=faster_rcnn, real_test_img_list=real_test_img_list, eval_data=eval_data, draw_imgs=showbox)


def parse_args():

    parser = argparse.ArgumentParser('evaluate the result with Pascal2007 stdand')

    parser.add_argument('--eval_data', dest='eval_data',
                        help='evaluate imgs dir ',
                        default='/unsullied/sharefs/_research_detection/GeneralDetection/COCO/data/MSCOCO/odformat/coco_minival2014.odgt', type=str)
    parser.add_argument('--json_file', dest='json_file',
                        help='test-dev json file',
                        default='/home/yangxue/isilon/yangxue/code/yxdet/FPN_Tensorflow/tools/image_info_test-dev2017.json', type=str)
    parser.add_argument('--showbox', dest='showbox',
                        help='whether show detecion results when evaluation',
                        default=False, type=bool)
    parser.add_argument('--GPU', dest='GPU',
                        help='gpu id',
                        default='0', type=str)
    parser.add_argument('--eval_num', dest='eval_num',
                        help='the num of eval imgs',
                        default=np.inf, type=int)
    args = parser.parse_args()
    return args


if __name__ == '__main__':

    # args = parse_args()
    # print(20*"--")
    # print(args)
    # print(20*"--")
    # os.environ["CUDA_VISIBLE_DEVICES"] = args.GPU
    # eval(np.inf,  # use np.inf to test all the imgs. use 10 to test 10 imgs.
    #      eval_data=args.eval_data,
    #      eval_gt=args.eval_gt,
    #      showbox=args.showbox)

    os.environ["CUDA_VISIBLE_DEVICES"] = '0'
    eval(np.inf,  # use np.inf to test all the imgs. use 10 to test 10 imgs.
         eval_data='/unsullied/sharefs/yangxue/isilon/yangxue/data/COCO/test2017',
         json_file='/home/yangxue/isilon/yangxue/code/yxdet/FPN_TF_DEV/tools/image_info_test-dev2017.json',
         showbox=False)

    # cocoval('./eval_coco/FPN_Res101_20190108_v1/coco_res.json',
    #         '/unsullied/sharefs/_research_detection/GeneralDetection/COCO/data/MSCOCO/instances_minival2014.json')



















