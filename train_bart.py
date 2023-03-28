import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
from pathlib import Path
from tqdm import tqdm
import numpy as np
import json
import csv

from utils import to_device, Checkpoint, Step, Smoother, Logger, EMA, FGM
from models_bart import TranslationModel
from dataset import TranslationDataset
from config_bart import Config
from losses import CE

from transformers import BartConfig, BartForConditionalGeneration

from evaluate import CiderD
# import wandb


def compute_batch(model, source, targets, verbose = False, optional_ret = []):
    source = to_device(source, 'cuda:0')
    targets = to_device(targets, 'cuda:0')
    losses = {}
    pred = model(source[:, :conf['input_l']], targets[:, :conf['output_l']])
    losses['loss_g'] = CE(pred[:, :-1], targets[:, 1:])
    return losses, pred

def array2str(arr):
    out = ''
    for i in range(len(arr)):
        if arr[i]==conf['pad_id'] or arr[i]==conf['eos_id']:
            break
        if arr[i]==conf['sos_id']:
            continue
        out += str(int(arr[i])) + ' '
    if len(out.strip())==0:
        out = '0'
    return out.strip()


def evaluate(model, loader, output_file=None, beam=1, n=-1):
    metrics = Smoother(100)
    res, gts = [], {}
    tot = 0
    for (source, targets) in tqdm(loader):
        if n>0 and tot>n:
            break
        source = to_device(source, 'cuda:0')
        pred = model(source, beam=beam)
        pred = pred.cpu().numpy()
        #print(pred.shape)
        for i in range(pred.shape[0]):
            res.append({'image_id':tot, 'caption': [array2str(pred[i])]})
            gts[tot] = [array2str(targets[i])]
            tot += 1
    CiderD_scorer = CiderD(df='corpus', sigma=15)
    cider_score, cider_scores = CiderD_scorer.compute_score(gts, res)
    metrics.update(cider = cider_score)
    print(metrics.value())
    return metrics

def get_model():
    return TranslationModel(conf['n_token'])
    # return BartForConditionalGeneration.from_pretrained('facebook/bart-base')

def train():
    train_data = TranslationDataset(conf['train_file'], conf['input_l'], conf['output_l'])
    valid_data = TranslationDataset(conf['valid_file'], conf['input_l'], conf['output_l'])

    train_loader = DataLoader(train_data, batch_size=conf['batch'], shuffle=True, num_workers=12, drop_last=False)
    valid_loader = DataLoader(valid_data, batch_size=conf['valid_batch'], shuffle=True, num_workers=12, drop_last=False)

    model = get_model()
    step = Step()
    checkpoint = Checkpoint(model = model, step = step)
    model = torch.nn.DataParallel(model)
    ema = EMA(model, 0.999, device="cuda:0")
    ema.register()
    fgm = FGM(model)
    model.to('cuda:0')

    optimizer = torch.optim.AdamW(model.parameters(), lr=conf['lr'])
    # scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=[6, 12, 24, 40], gamma=0.5)

    start_epoch = 0
    
    train_loss = Smoother(100)
    adv_train_loss = Smoother(100)
    logger = Logger(conf['model_dir']+'/log%d.txt'%version, 'a')
    logger.log(conf)
    writer = SummaryWriter(conf['model_dir'])
    
    Path(conf['model_dir']).mkdir(exist_ok=True, parents=True)
    for epoch in range(start_epoch, conf['n_epoch']):
        print('epoch', epoch)
        logger.log('new epoch', epoch)
        for (source, targets) in tqdm(train_loader):
            step.forward(source.shape[0])
            
            losses, pred = compute_batch(model, source, targets)
            loss = torch.FloatTensor([0]).to('cuda:0')
            for x in losses:
                if x[:5]=='loss_':
                    loss += eval('conf["w_%s"]'%x[5:])*losses[x]
            losses['loss'] = loss
            train_loss.update(loss={x:losses[x].item() for x in losses})
            loss.backward()

            fgm.attack()
            adv_losses, _ = compute_batch(model, source, targets)
            adv_loss = torch.FloatTensor([0]).to('cuda:0')
            for x in adv_losses:
                if x[:5]=='loss_':
                    adv_loss += eval('conf["w_%s"]'%x[5:])*adv_losses[x]
            adv_losses['loss'] = adv_loss
            adv_train_loss.update(adv_loss={x:adv_losses[x].item() for x in adv_losses})
            adv_loss.backward()
            fgm.restore()

            optimizer.step() #优化一次
            ema.update()
            optimizer.zero_grad() #清空梯度

            if step.value%100==0:
                logger.log(step.value, train_loss.value())
                logger.log(array2str(targets[0].cpu().numpy()))
                logger.log(array2str(torch.argmax(pred[0], 1).cpu().numpy()))
                # wandb.log({'step': step.value})
                # wandb.log({'train_loss': train_loss.value(), 'lr': optimizer.param_groups[0]['lr']})
        ema.apply_shadow()
        if epoch%6==0:
            checkpoint.save(conf['model_dir']+'/model_%d.pt'%epoch)
            model.eval()
            metrics = evaluate(model, valid_loader)
            logger.log('valid', step.value, metrics.value())
            # wandb.log({'valid_metric': metrics.value()})
            writer.add_scalars('valid metric', metrics.value(), step.value)
            checkpoint.update(conf['model_dir']+'/model.pt', metrics = metrics.value())
            model.train()

        # scheduler.step()
        # wandb.log({'epoch': epoch})
        ema.restore()
    logger.close()
    writer.close()
    
def inference(model_file, data_file):
    test_data = TranslationDataset(data_file, conf['input_l'], conf['output_l'])
    test_loader = DataLoader(test_data, batch_size=conf['valid_batch'], shuffle=False, num_workers=1, drop_last=False)

    model = get_model()
    checkpoint = Checkpoint(model = model)
    checkpoint.resume(model_file)
    
    model = nn.DataParallel(model)
    model.to('cuda:0')
    model.eval()
    
    fp = open('pred.csv', 'w', newline='')
    writer = csv.writer(fp)
    tot = 0
    for source in tqdm(test_loader):
        source = to_device(source, 'cuda:0')
        pred = model(source, beam=1)
        pred = pred.cpu().numpy()
        for i in range(pred.shape[0]):
            writer.writerow([tot, array2str(pred[i])])
            tot += 1
    fp.close()

version = 1
conf = Config(version)

# wandb.init(
#         project="2023GAIIC",
#         name="bart",
# )

train()
# inference('checkpoint/%d/model_cider.pt'%version, conf['test_file'])