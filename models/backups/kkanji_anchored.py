import os
import argparse

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.transforms as tvt

from data.kkanji import *

from utils.misc import add_args
from utils.paths import datasets_path, benchmarks_path

from neural.attention import StackedISAB, PMA, MAB, ISAB
from neural.modules import FixupResUnit, View, ConvResUnit

from models.base import ModelTemplate

from data.clustered_dataset import get_random_data_loader, get_saved_data_loader
from data.cluster import sample_anchor

# for training
parser = argparse.ArgumentParser()
parser.add_argument('--B', type=int, default=100)
parser.add_argument('--N', type=int, default=50)
parser.add_argument('--K', type=int, default=4)
parser.add_argument('--lr', type=float, default=5e-4)
parser.add_argument('--num_steps', type=int, default=80000)
parser.add_argument('--testfile', type=str, default=None)
parser.add_argument('--clusterfile', type=str, default=None)

sub_args, _ = parser.parse_known_args()

class FindCluster(nn.Module):
    def __init__(self, dim_hids=128, num_inds=32):
        super().__init__()

        self.encoder = nn.Sequential(
                FixupResUnit(1, 32, stride=2),
                FixupResUnit(32, 64, stride=2),
                FixupResUnit(64, dim_hids, stride=2),
                nn.AdaptiveAvgPool2d(1))

        self.mab1 = MAB(dim_hids, dim_hids, dim_hids)
        self.isab = StackedISAB(dim_hids, dim_hids, num_inds, 4)
        self.mab2 = MAB(dim_hids, dim_hids, dim_hids)
        self.fc = nn.Linear(dim_hids, 1)

    def forward(self, X, anchor_idx):
        B, N, C, H, W = X.shape
        H_enc = self.encoder(X.view(B*N, C, H, W)).view(B, N, -1)
        anchors = H_enc[torch.arange(B), anchor_idx].unsqueeze(1)
        H_enc = self.isab(self.mab1(H_enc, anchors))
        return self.fc(self.mab2(H_enc, anchors)).squeeze(-1)

class Model(ModelTemplate):
    def __init__(self, args):
        super().__init__(args)
        self.net = FindCluster()
        self.metrics = ['bcent']

    @classmethod
    def get_Kkanji(cls, train=True):
        transform = tvt.Normalize(mean=[0.2170], std=[0.3787])
        return Kkanji(os.path.join(datasets_path, 'kkanji'),
                train=train, transform=transform)

    def sample(self, B, N, K, **kwargs):
        loader = get_random_data_loader(
                self.get_Kkanji(False),
                B, N, K, 1, TEST_NUM_PER_CLASS, **kwargs)
        return next(iter(loader))

    def loss_fn(self, dataset, train=True):
        X = dataset['X'].cuda()
        labels = dataset['labels'].cuda().argmax(-1)
        anchors = sample_anchor(labels) if train else dataset['anchors']
        logits = self.net(X, anchors)
        anchor_labels = labels[torch.arange(X.shape[0]), anchors]
        targets = (labels == anchor_labels.unsqueeze(-1)).float()
        bcent = F.binary_cross_entropy_with_logits(logits, targets)
        return bcent

    def get_train_loader(self):
        return get_random_data_loader(
                self.get_Kkanji(),
                self.B, self.N, self.K, self.num_steps,
                TRAIN_NUM_PER_CLASS)

    def get_test_loader(self, filename=None):
        filename = self.testfile if filename is None else filename
        return get_saved_data_loader(
                self.get_Kkanji(False),
                filename, TEST_NUM_PER_CLASS)

def load(args):
    add_args(args, sub_args)
    args.testfile = os.path.join(benchmarks_path,
            'kkanji_10_100_4.tar' if args.testfile is None else args.testfile)
    args.clusterfile = os.path.join(benchmarks_path,
            'kkanji_10_400_12.tar' if args.clusterfile is None else args.clusterfile)
    return Model(args)

if __name__ == '__main__':

    model = load(sub_args)
    dataset = model.sample(1, 50, 4, rand_K=False)
    X = dataset['X']
    labels = dataset['labels'].argmax(-1)
    from torchvision.utils import make_grid
    import matplotlib.pyplot as plt

    plt.figure()
    img = make_grid(X[0][labels[0]==0])
    plt.imshow(img.numpy().transpose(1,2,0))
    plt.figure()
    img = make_grid(X[0][labels[0]==1])
    plt.imshow(img.numpy().transpose(1,2,0))
    plt.show()
