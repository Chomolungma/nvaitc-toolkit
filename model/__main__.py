# The MIT License (MIT)

# Copyright (c) 2020 NVIDIA CORPORATION.

# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import os
import random
import shutil
import time
import warnings
import sys

from apex import amp
from argparse import ArgumentParser
from torch2trt import torch2trt, TRTModule
from torchvision import datasets, transforms, models

import horovod.torch as hvd
import re
import torch
import torch.optim as optim

from model.launchers import Tester, DALITrainer, TVTrainer
from model.loader.loaders import ImageNetTrainPipe, ImageNetValPipe
from model.util import timeme

import torchvision.transforms as transforms
import torchvision.datasets as datasets
import torchvision.models as models

import torch.backends.cudnn as cudnn

import torch.multiprocessing as mp

import numpy as np

from torch.utils.tensorboard import SummaryWriter

import pprint

try:
    from nvidia.dali.plugin.pytorch import DALIClassificationIterator
    from nvidia.dali.pipeline import Pipeline
    import nvidia.dali.ops as ops
    import nvidia.dali.types as types
except ImportError:
    raise ImportError("Please install DALI from https://www.github.com/NVIDIA/DALI to run this example.")


@timeme
def process_train(args, cfg=None):

    crop_size = 224
    val_size = 256
    
    # Scale learning rate based on global batch size
    args.lr = args.lr*float(args.batch_size*args.world_size)/256.

    traindir = os.path.join(args.data_dir, 'train')
    valdir = os.path.join(args.data_dir, 'val')

    if args.arch == 'resnet50':
        network = models.resnet50()
    elif args.arch == 'resnet101':
        network = models.resnet101()
    elif args.arch == 'resnet18':
        network = models.resnet18()
    else:
        if args.local_rank == 0:
            print('No network specified')
        sys.exit()

    if args.local_rank == 0:
        print("= Start training =")
        print("=> Arch '{}'".format(args.arch))

    # TODO 
    if args.sync_bn:
        print("using apex synced BN")
        model = parallel.convert_syncbn_model(model)

    # FIXME: understand if needed
    if hasattr(torch, 'channels_last') and  hasattr(torch, 'contiguous_format'):
        if args.channels_last:
            memory_format = torch.channels_last
        else:
            memory_format = torch.contiguous_format
        network = network.cuda().to(memory_format=memory_format)
    else:
        network = network.cuda()


    # Instantiate distributed SGD optimizer
    optimizer = optim.SGD(network.parameters(), args.lr,
                                momentum=args.momentum,
                                weight_decay=args.weight_decay)

    # Horovod: (optional) compression algorithm.
    compression = hvd.Compression.fp16 if args.fp16_allreduce else hvd.Compression.none
    # Horovod: wrap optimizer with DistributedOptimizer.

    if args.local_rank == 0:
        print("=> world size '{}'".format(args.world_size))



    if args.distributed:
        optimizer = hvd.DistributedOptimizer(
            optimizer, named_parameters=network.named_parameters(),
            compression=compression,
            backward_passes_per_step=args.batches_per_allreduce,
            op=hvd.Adasum if args.use_adasum else hvd.Average)


    # Option for Apex/AMP multiprecision training
    if args.amp:
        network, optimizer = amp.initialize(network, optimizer,
                opt_level=args.opt_level,
                keep_batchnorm_fp32=args.keep_batchnorm_fp32,
                loss_scale=args.loss_scale
                )

    resume_from_epoch = args.start_epoch

    args.best_prec1 = 0

    # Optionally resume from a checkpoint
    if args.resume:
        # Use a local scope to avoid dangling references
        def resume():
            if os.path.isfile(args.resume):
                print("=> loading checkpoint '{}'".format(args.resume))
                checkpoint = torch.load(args.resume, map_location = lambda storage, loc: storage.cuda(args.gpu))
                args.start_epoch = checkpoint['epoch']
                args.best_prec1 = checkpoint['best_prec1']
                print("=> best precision '{}'".format(args.best_prec1))
                network.load_state_dict(checkpoint['state_dict'])
                optimizer.load_state_dict(checkpoint['optimizer'])
                print("=> loaded checkpoint '{}' (epoch {})"
                      .format(args.resume, checkpoint['epoch']))
            else:
                print("=> no checkpoint found at '{}'".format(args.resume))
        resume()

        # Horovod: broadcast resume_from_epoch from rank 0 (which will have
        # checkpoints) to other ranks.
        hvd.broadcast(torch.tensor(resume_from_epoch), root_rank=0, name='resume_from_epoch') 

    # To go before AMP initialize
    # Horovod: broadcast parameters & optimizer state.
    hvd.broadcast_parameters(network.state_dict(), root_rank=0)
    hvd.broadcast_optimizer_state(optimizer, root_rank=0)

    if args.loader == 'dali':
        if args.local_rank == 0:
            print('Using DALI as data loader')
        #train = get_loader('train_loader', args.batch_size, hvd.local_rank(), hvd.size(), traindir, **kwargs)    
        #train.build()
        #train_loader = DALIClassificationIterator(train, reader_name="Reader", fill_last_batch=False)

        #val = get_loader('val_loader', args.batch_size, hvd.local_rank(), hvd.size(), valdir, **kwargs)    
        #val.build()
        #val_loader = DALIClassificationIterator(val, reader_name="Reader", fill_last_batch=False)

        pipe = ImageNetTrainPipe(batch_size=args.batch_size, 
            num_threads=args.workers,
            device_id=args.local_rank,
            data_dir=traindir,
            crop=crop_size,
            dali_cpu=args.dali_cpu,
            shard_id=args.local_rank,
            num_shards=args.world_size)
        pipe.build()

        train_loader = DALIClassificationIterator(pipe, reader_name="Reader", fill_last_batch=False)

        pipe = ImageNetValPipe(batch_size=args.batch_size,
                            num_threads=args.workers,
                            device_id=args.local_rank,
                            data_dir=valdir,
                            crop=crop_size,
                            size=val_size,
                            shard_id=args.local_rank,
                            num_shards=args.world_size)
        pipe.build()
        val_loader = DALIClassificationIterator(pipe, reader_name="Reader", fill_last_batch=False)        

        launcher = DALITrainer(args, train_loader, val_loader, network, optimizer)
        launcher.run()

    elif args.loader == 'torchvision':

        if args.local_rank == 0:
            print('Using TorchVision as data loader')
        
        train_dataset = datasets.ImageFolder(
            traindir,
            transforms.Compose([
                transforms.RandomResizedCrop(crop_size),
                transforms.RandomHorizontalFlip(),
                # transforms.ToTensor(), Too slow
                # normalize,
            ]))

        val_dataset = datasets.ImageFolder(valdir, transforms.Compose([
                transforms.Resize(val_size),
                transforms.CenterCrop(crop_size),
            ]))            

        # Horovod: use DistributedSampler to partition data among workers. Manually specify
        # `num_replicas=hvd.size()` and `rank=hvd.rank()`.

        train_sampler = None
        val_sampler = None
        
        if args.distributed:
            train_sampler = torch.utils.data.distributed.DistributedSampler(
                train_dataset, num_replicas=hvd.size(), rank=hvd.rank())
            val_sampler = torch.utils.data.distributed.DistributedSampler(
                val_dataset, num_replicas=hvd.size(), rank=hvd.rank())

        collate_fn = lambda b: fast_collate(b, memory_format)

        train_loader = torch.utils.data.DataLoader(
            train_dataset, batch_size=args.batch_size, shuffle=(train_sampler is None),
            num_workers=args.workers, pin_memory=True, sampler=train_sampler, collate_fn=collate_fn)

        val_loader = torch.utils.data.DataLoader(
            val_dataset,
            batch_size=args.batch_size, shuffle=False,
            num_workers=args.workers, pin_memory=True,
            sampler=val_sampler,
            collate_fn=collate_fn)        
    
        launcher = TVTrainer(args, train_loader, train_sampler, val_loader, network, optimizer)
        launcher.run()

    else:
        print('No correct loader specified')


def process_test(args, cfg):
    cfg['load_path'] = cfg['test_path']

    hvd.init()
    torch.cuda.set_device(hvd.local_rank())

    # One shard
    loader = get_loader(cfg, hvd.local_rank(), 1, split='test')

    if args.state.endswith('.trt'):
        network = TRTModule()
        network.load_state_dict(torch.load(args.state))
    else:
        network = torch.load(args.state)
        network.eval()
    network.cuda()

    launcher = Tester(cfg, loader, network, None)
    launcher.run()


def process_cam(args, cfg):
    cfg['load_path'] = cfg['test_path']
    cfg['batch_size'] = 1

    hvd.init()
    torch.cuda.set_device(hvd.local_rank())

    loader = get_loader(cfg, hvd.local_rank(), 1, split='test')

    network = torch.load(args.state)
    network.eval()

    optimizer = optim.Adam(network.parameters())

    launcher = Tester(cfg, loader, network, optimizer)
    launcher.cam()


def process_convert(args, cfg):
    state = torch.load(args.state)
    path = args.state.replace('.pt', '.trt')
    bs = cfg['batch_size']

    # Three channels
    dummy_input = torch.zeros(bs, 3, *cfg['image_size'])
    dummy_input = dummy_input.type(torch.cuda.FloatTensor)

    state_trt = torch2trt(state, [dummy_input], max_batch_size=bs)
    torch.save(state_trt.state_dict(), path)


def main():

    ap = ArgumentParser(description='New Image Classifier')
    sp = ap.add_subparsers(dest='cmd')

    # Run from scratch
    ap_run = sp.add_parser('train')
    ap_run.add_argument('--log-dir', default='./logs', 
            help='tensorboard log directory')
    ap_run.add_argument('--epochs', type=int, default=90,
                    help='number of epochs to train')
    ap_run.add_argument('--base-lr', type=float, default=0.0125,
                    help='learning rate for a single GPU')     
    ap_run.add_argument('--momentum', type=float, default=0.9,
                    help='SGD momentum')
    ap_run.add_argument('--lr', '--learning-rate', default=0.1, type=float,
                        metavar='LR', help='Initial learning rate.  Will be scaled by <global batch size>/256: args.lr = args.lr*float(args.batch_size*args.world_size)/256.  A warmup schedule will also be applied over the first 5 epochs.')
    ap_run.add_argument('--batches-per-allreduce', type=int, default=1,
                    help=('number of batches processed locally before '
                         + 'executing allreduce across workers; it multiplies '
                         + 'total batch size.'))       
    ap_run.add_argument('--use-adasum', action='store_true', default=False,
                    help='use adasum algorithm to do reduction')       
    ap_run.add_argument('-b', '--batch-size', default=256, type=int,
                    metavar='N', help='mini-batch size per process (default: 256)')
    ap_run.add_argument('-j', '--workers', default=4, type=int, metavar='N',
                    help='number of data loading workers (default: 4)')                    
    ap_run.add_argument('--print-freq', type=int, default=10,
                    help='output frequency')
    ap_run.add_argument('--sync_bn', action='store_true',
                    help='enabling apex sync BN.')        
    ap_run.add_argument('--weight-decay', '--wd', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)')           
    ap_run.add_argument('--data-dir', default='/workspace/imagenet', 
                    help='data loading path')
    ap_run.add_argument('--loss-scale', type=str, default=None)       
    ap_run.add_argument('--warmup-epochs', type=float, default=5,
                        help='number of warmup epochs')
    ap_run.add_argument('--start-epoch', default=0, type=int, metavar='N',
                        help='manual epoch number (useful on restarts)')

    ap_run.add_argument('--resume', default='', type=str, metavar='PATH',
                        help='path to latest checkpoint (default: none)')

    ap_run.set_defaults(process=process_train)


    # Test model
    ap_restart = sp.add_parser('test')
    ap_restart.add_argument('state', help='state file')
    ap_restart.set_defaults(process=process_test)

    # Run CAM
    ap_restart = sp.add_parser('cam')
    ap_restart.add_argument('state', help='state file')
    ap_restart.set_defaults(process=process_cam)

    # Convert to TensorRT
    ap_convert = sp.add_parser('convert')
    ap_convert.add_argument('state', help='state file')
    ap_convert.set_defaults(process=process_convert)
    
    # Mixed precision
    ap.add_argument('--amp', '-a', action='store_true')
    ap.add_argument('--opt-level', type=str, default="O1")
    ap.add_argument('--keep-batchnorm-fp32', type=str, default=None)
    ap.add_argument('--loss-scale', type=str, default=None)
    ap.add_argument('--channels-last', type=bool, default=False)

    # Loader
    ap.add_argument('-dl', '--loader', type=str, default="dali")

    # Arch
    ap.add_argument('-ar', '--arch', type=str, default="resnet50")

    # Deterministic runtime
    ap.add_argument('--deterministic', action='store_true')

    ap.add_argument('--fp16-allreduce', action='store_true', default=False,
                    help='use fp16 compression during allreduce')

    # CPU Based DALI pipeline
    ap.add_argument('--dali_cpu', action='store_true', 
        help='Runs CPU based version of DALI pipeline.')

    # Profiling NVTX
    ap.add_argument('--prof', default=-1, type=int,
                        help='Only run 10 iterations for profiling.')

    args = ap.parse_args()

    args.no_cuda = False

    args.cuda = not args.no_cuda and torch.cuda.is_available()

    hvd.init()

    # Horovod: limit # of CPU threads to be used per worker.
    #torch.set_num_threads(4)

    if args.cuda:
        # Horovod: pin GPU to local rank.
        torch.cuda.set_device(hvd.local_rank())

    args.local_rank = hvd.local_rank()
    args.gpu = args.local_rank
    args.world_size = hvd.size()

    args.distributed = args.world_size > 1

    if args.distributed:
        torch.cuda.set_device(args.gpu)
        #torch.distributed.init_process_group(backend='nccl',
        #                                     init_method='env://')

    # Enable cudnn rk
    cudnn.benchmark = True

    if args.deterministic:
        cudnn.benchmark = False
        cudnn.deterministic = True
        torch.manual_seed(args.local_rank)
        torch.set_printoptions(precision=10)

    assert torch.backends.cudnn.enabled, "Amp requires cudnn backend to be enabled."

    args.total_batch_size = args.world_size * args.batch_size

    args.allreduce_batch_size = args.batch_size * args.batches_per_allreduce    

    cfg = {}

    if hasattr(args, 'process'):
        args.process(args, cfg)
    else:
        ap.print_help()

def fast_collate(batch, memory_format):

    imgs = [img[0] for img in batch]
    targets = torch.tensor([target[1] for target in batch], dtype=torch.int64)
    w = imgs[0].size[0]
    h = imgs[0].size[1]
    tensor = torch.zeros( (len(imgs), 3, h, w), dtype=torch.uint8).contiguous(memory_format=memory_format)
    for i, img in enumerate(imgs):
        nump_array = np.asarray(img, dtype=np.uint8)
        if(nump_array.ndim < 3):
            nump_array = np.expand_dims(nump_array, axis=-1)
        nump_array = np.rollaxis(nump_array, 2)
        tensor[i] += torch.from_numpy(nump_array)
    return tensor, targets

if __name__ == '__main__': 
    main()
