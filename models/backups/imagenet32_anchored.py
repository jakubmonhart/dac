import os
import argparse
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.transforms as tvt

from data.cluster import sample_anchor
from utils.misc import add_args
from utils.paths import datasets_path, benchmarks_path

from neural.attention import StackedISAB, PMA, MAB
from neural.modules import FixupResUnit, View, ConvResUnit

from models.base import ModelTemplate

from data.clustered_dataset import get_random_data_loader, get_saved_data_loader
from data.imagenet32 import *

# for training
parser = argparse.ArgumentParser()
parser.add_argument('--B', type=int, default=500)
parser.add_argument('--N', type=int, default=32)
parser.add_argument('--K', type=int, default=4)
parser.add_argument('--lr', type=float, default=3e-4)
parser.add_argument('--novel', action='store_true')
parser.add_argument('--num_steps', type=int, default=200000)
parser.add_argument('--testfile', type=str, default=None)
parser.add_argument('--clusterfile', type=str, default=None)

sub_args, _ = parser.parse_known_args()

class FindCluster(nn.Module):
    def __init__(self, dim_hids=256, num_inds=32):
        super().__init__()

        self.encoder = nn.Sequential(
                FixupResUnit(3, 16, stride=2),
                FixupResUnit(16, 32, stride=2),
                FixupResUnit(32, 64, stride=2),
                FixupResUnit(64, dim_hids),
                nn.AdaptiveAvgPool2d(1))

        self.mab = MAB(dim_hids, dim_hids, dim_hids)
        self.isab = StackedISAB(dim_hids, dim_hids, num_inds, 6)
        self.fc = nn.Linear(dim_hids, 1)

    def forward(self, X, anchor_idx):
        B, N, C, H, W = X.shape
        H_enc = self.encoder(X.view(B*N, C, H, W)).view(B, N, -1)
        anchors = H_enc[torch.arange(B), anchor_idx].unsqueeze(1)
        H_enc = self.mab(H_enc, anchors)
        return self.fc(self.isab(H_enc)).squeeze(-1)

class Model(ModelTemplate):
    def __init__(self, args):
        super().__init__(args)
        self.net = FindCluster()
        self.metrics = ['bcent']

    def get_ImageNet32(self, train=True):
        transforms = [tvt.ToTensor(),
                tvt.Normalize(mean=MEAN, std=STD)]
        transform = tvt.Compose(transforms)
        return ImageNet32(ROOT, train=train, transform=transform)

    def get_train_loader(self):
        return get_random_data_loader(
                self.get_ImageNet32(),
                self.B, self.N, self.K, self.num_steps,
                TRAIN_NUM_PER_CLASS,
                classes=range(TRAIN_NUM_CLASSES))

    def get_test_loader(self, filename=None):
        filename = self.testfile if filename is None else filename
        classes = range(TRAIN_NUM_CLASSES, NUM_CLASSES) if self.novel \
                else range(TRAIN_NUM_CLASSES)
        return get_saved_data_loader(
                self.get_ImageNet32(False),
                filename, TEST_NUM_PER_CLASS,
                classes=classes)

    def loss_fn(self, dataset, train=True):
        X = dataset['X'].cuda()
        labels = dataset['labels'].cuda().argmax(-1)
        anchor_idx = sample_anchor(labels) if train else dataset['anchors']
        logits = self.net(X, anchor_idx)
        anchor_labels = labels[torch.arange(X.shape[0]), anchor_idx]
        targets = (labels == anchor_labels.unsqueeze(-1)).float()
        bcent = F.binary_cross_entropy_with_logits(logits, targets)
        return bcent

def load(args):
    add_args(args, sub_args)

    if args.novel:
        args.testfile = os.path.join(benchmarks_path,
                'imagenet32_novel_10_200_4.tar' if args.testfile is None else args.testfile)
        args.clusterfile = os.path.join(benchmarks_path,
                'imagenet32_novel_10_600_12.tar' if args.clusterfile is None else args.clusterfile)
    else:
        args.testfile = os.path.join(benchmarks_path,
                'imagenet32_10_200_4.tar' if args.testfile is None else args.testfile)
        args.clusterfile = os.path.join(benchmarks_path,
                'imagenet32_10_600_12.tar' if args.clusterfile is None else args.clusterfile)
    return Model(args)
