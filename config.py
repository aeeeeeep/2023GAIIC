
class Config(dict):
    def version_config(self, version):
        batch = 100
        hp = {1: {'n_epoch':50, 'batch': batch, 'valid_batch': batch, 'n_layer':6},
              }
        self['n_epoch'] = hp[version].get('n_epoch', 10)
        self['n_layer'] = hp[version].get('n_layer', 6)
        self['batch'] = hp[version].get('batch', batch)
        self['valid_batch'] = hp[version].get('valid_batch', batch)
        self['w_g'] = 1

        #请自己造训练测试集
        self['train_file'] = 'data/try.csv'
        self['valid_file'] = 'data/val.csv'
        self['test_file'] = 'data/preliminary_a_test.csv'
    
        self['input_l'] = 150
        self['output_l'] = 80
        self['n_token'] = 1500
        self['sos_id'] = 1
        self['eos_id'] = 2
        self['pad_id'] = 0
        
    def __init__(self, version, seed=0):
        self['lr'] = 1e-4
        self['model_dir'] = './checkpoint/%d'%version
        if seed>0:
            self['model_dir'] += '_%d'%seed
        self['output_dir'] = './outputs/%d'%version
        
        self.version_config(version)
