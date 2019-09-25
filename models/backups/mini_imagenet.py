import os
import argparse
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.transforms as tvt

from utils.misc import add_args
from utils.paths import datasets_path, benchmarks_path

from neural.attention import StackedISAB, PMA, MAB
from neural.modules import FixupResUnit, View

from models.base import ModelTemplate

from data.clustered_dataset import get_random_data_loader, get_saved_data_loader
from data.mini_imagenet import MiniImagenet, TRAIN_NUM_PER_CLASS, TEST_NUM_PER_CLASS

# for training
parser = argparse.ArgumentParser()
parser.add_argument('--B', type=int, default=100)
parser.add_argument('--N', type=int, default=32)
parser.add_argument('--K', type=int, default=4)
parser.add_argument('--lr', type=float, default=3e-4)
parser.add_argument('--novel', action='store_true')
parser.add_argument('--num_steps', type=int, default=80000)
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

        self.isab = StackedISAB(dim_hids, dim_hids, num_inds, 6)
        self.fc = nn.Linear(dim_hids, 1)

    def forward(self, X, mask=None):
        B, N, C, H, W = X.shape
        #X = X - X.mean(1, keepdim=True)
        X_flat = X.view(B*N, C, H, W)
        H_enc = self.encoder(X_flat).view(B, N, -1)
        #H_enc = H_enc - H_enc.mean(1, keepdim=True)
        H_enc = self.isab(H_enc, mask=mask)
        return self.fc(H_enc)

class Model(ModelTemplate):
    def __init__(self, args):
        super().__init__(args)
        self.net = FindCluster()
        self.metrics = ['bcent']

    def get_MiniImagenet(self, train=True):
        transforms = [tvt.ToTensor(),
                tvt.Normalize(
                    mean=[0.485, 0.456, 0.406],
                    std=[0.229, 0.224, 0.225])]
        #if train:
        #    transforms = [tvt.RandomHorizontalFlip(),
        #            tvt.RandomVerticalFlip(),
        #            tvt.ColorJitter()] + transforms
        transform = tvt.Compose(transforms)
        return MiniImagenet(os.path.join(datasets_path, 'mini-imagenet'),
                split = 'train' if train else \
                        ('test' if self.novel else 'test_overlap'),
                transform=transform)

    def sample(self, B, N, K, **kwargs):
        dataset = self.get_MiniImagenet(train=False)
        loader = get_random_data_loader(
                self.get_MiniImagenet(False),
                B, N, K, 1, TEST_NUM_PER_CLASS, **kwargs)
        return next(iter(loader))

    def get_train_loader(self):
        return get_random_data_loader(
                self.get_MiniImagenet(),
                self.B, self.N, self.K, self.num_steps,
                TRAIN_NUM_PER_CLASS)

    def get_test_loader(self):
        return get_saved_data_loader(
                self.get_MiniImagenet(False), self.testfile,
                TEST_NUM_PER_CLASS)

    def loss_fn(self, dataset, train=True):
        X = dataset['X'].cuda()
        logits = self.net(X)
        labels = dataset['labels'].float().cuda()
        K = labels.shape[-1]
        bcent = F.binary_cross_entropy_with_logits(
                logits.repeat(1, 1, K), labels, reduction='none').mean(1)
        bcent[labels.sum(1)==0] = float('inf')
        bcent, idx = bcent.min(1)
        bidx = bcent != float('inf')
        return bcent[bidx].mean()

def load(args):
    add_args(args, sub_args)

    if args.novel:
        args.testfile = os.path.join(benchmarks_path,
                'mini_imagenet_novel_10_200_4.tar' if args.testfile is None else args.testfile)
        args.clusterfile = os.path.join(benchmarks_path,
                'mini_imagenet_novel_10_600_12.tar' if args.clusterfile is None else args.clusterfile)
    else:
        args.testfile = os.path.join(benchmarks_path,
                'mini_imagenet_10_200_4.tar' if args.testfile is None else args.testfile)
        args.clusterfile = os.path.join(benchmarks_path,
                'mini_imagenet_10_600_12.tar' if args.clusterfile is None else args.clusterfile)
    return Model(args)

if __name__ == '__main__':
    model = load(sub_args)
    from torchvision.utils import make_grid
    import matplotlib.pyplot as plt

    train_loader = model.get_train_loader()
    dataset = next(iter(train_loader))
    labels = dataset['labels'].argmax(-1)
    print(dataset['classes'][:2])

    for b in range(2):
        plt.figure('train {}'.format(b))
        img = make_grid(dataset['X'][b][labels[b]==0])
        plt.imshow(img.numpy().transpose(1,2,0))

    test_loader = model.get_test_loader()
    dataset = next(iter(test_loader))
    labels = dataset['labels'].argmax(-1)
    print(dataset['classes'][:2])
    for b in range(2):
        plt.figure('test {}'.format(b))
        img = make_grid(dataset['X'][b][labels[b]==0])
        plt.imshow(img.numpy().transpose(1,2,0))

    plt.show()
