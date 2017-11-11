import logging
from collections import OrderedDict

import tensorflow as tf

from model import clone_variable

from evaluator import create_adv_by_name
from trainer import TrainManager
from runner import RunnerMultiGPU


class TrainerMultiGPU(TrainManager):
    def __init__(self, *args, **kwargs):
        super(TrainerMultiGPU, self).__init__(*args, **kwargs)
        self.runner = RunnerMultiGPU(self.inputs, self.outputs, sess=self.sess)

    def create_train_graph(self):
        assert self.evaluate is None, ("""Evaluation graph should be initialzed
                                       after the train graph""")
        assert '_multigpu' in self.hparams.attack_type_train

        hparams = self.hparams
        model = self.model
        sess = self.sess

        # Generates steps on gpus
        model.set_training(training=False)
        logging.info("Initializing train attack %s" %
                     hparams.attack_type_train)
        inputs, outputs = create_adv_by_name(
            model, self.g0_inputs['x'], hparams.attack_type_train,
            sess, y=self.g0_inputs['y'], nb_iter=hparams.attack_nb_iter_train,
            dataset=hparams.dataset, ngpu=hparams.ngpu,
            g0_inputs=self.g0_inputs)

        assert len(inputs) == len(outputs)

        # train step on last gpu
        device_name = '/gpu:%d' % (hparams.ngpu-1)
        model.set_device(device_name)
        with tf.device(device_name):
            with tf.variable_scope('last'):
                inputs += [OrderedDict()]
                for k, v in outputs[-1].iteritems():
                    v_copy = clone_variable(k, v)
                    inputs[-1][k] = v_copy
                x = inputs[-1]['x']
                adv_x = inputs[-1]['adv_x']
                y = inputs[-1]['y']
                if not hparams.adv_train:
                    model.set_training(training=True, bn_training=True)
                    preds = model.get_probs(x)
                    preds_adv = None
                elif not hparams.only_adv_train:
                    model.set_training(training=True)
                    preds = model.get_probs(x)
                    model.set_training(training=True, bn_training=True)
                    preds_adv = model.get_probs(adv_x)
                else:
                    preds = None
                    model.set_training(training=True, bn_training=True)
                    preds_adv = model.get_probs(adv_x)
                train_fetches = self.build_train_op(preds, y, preds_adv)

        outputs += [{'fetches': train_fetches}]

        device_name = '/gpu:%d' % (hparams.ngpu-1)
        model.set_device(device_name)
        with tf.device(device_name):
            sync_ops = model.create_sync_ops(host_device=device_name)

        self.inputs = inputs
        self.outputs = outputs
        self.sync_ops = sync_ops

    def sync_params(self, forced=False):
        if forced or (self.step_num % self.hparams.sync_step == 0):
            self.sess.run(self.sync_ops)
