#!/usr/bin/env python

# Notification Center code borrowed from https://github.com/maranas/pyNotificationCenter/blob/master/pyNotificationCenter.py

VERSION =   '1.3.1'

import os, sys, json, re, hashlib, argparse, urllib, time, base64, ConfigParser, gzip, mimetypes, zipfile
from xml.dom import minidom

# disable import warnings
import warnings
warnings.filterwarnings('ignore')

DEFAULT_COLOR   =   '\033[0;0m'
ERROR_COLOR     =   '\033[01;31m'
ALERT_COLOR     =   '\033[01;33m'

def alert(text, error_code = None, color = None):
    if error_code is not None:
        sys.stderr.write('%s%s%s\n' % (color or ERROR_COLOR, text, DEFAULT_COLOR))
        sys.stderr.flush()
        sys.exit(error_code)
    else:
        sys.stdout.write('%s%s%s\n' % (color or DEFAULT_COLOR, text, DEFAULT_COLOR))
        sys.stdout.flush()

# check for updates
PYPI_URL        =   'https://pypi.python.org/pypi?:action=doap&name=d3ploy'
CHECK_FILE      =   os.path.expanduser('~/.d3ploy-update-check')
if not os.path.exists(CHECK_FILE):
    try:
        open(CHECK_FILE, 'w')
    except IOError:
        pass
try:
    last_checked    =   int(open(CHECK_FILE, 'r').read().strip())
except ValueError:
    last_checked    =   0
now =   int(time.time())
if now - last_checked > 86400:
    # it has been a day since the last update check
    try:
        pypi_data       =    minidom.parse(urllib.urlopen(PYPI_URL))
        pypi_version    =    pypi_data.getElementsByTagName('revision')[0].firstChild.data
        if pypi_version > VERSION:
            alert('There has been an update for d3ploy. Version %s is now available.\nPlease see https://github.com/dryan/d3ploy or run `pip install --upgrade d3ploy`.' % pypi_version, color = ALERT_COLOR)
    except:
        pass
    check_file  =   open(CHECK_FILE, 'w')
    check_file.write(str(now))
    check_file.flush()
    check_file.close()

try:
    import boto
except ImportError:
    alert("Please install boto. `pip install boto`", os.EX_UNAVAILABLE)
    
try:
    import Foundation, objc
    notifications   =   True
except ImportError:
    notifications   =   False
    
if notifications:
    try:
        NSUserNotification          =   objc.lookUpClass('NSUserNotification')
        NSUserNotificationCenter    =   objc.lookUpClass('NSUserNotificationCenter')
    except objc.nosuchclass_error:
        notifications   =   False
        
def notify(env, text):
    alert(text)
    if notifications:
        notification    =   NSUserNotification.alloc().init()
        notification.setTitle_('d3ploy')
        notification.setSubtitle_(env)
        notification.setInformativeText_(text)
        notification.setUserInfo_({})
        if os.environ.get('D3PLOY_NC_SOUND'):
            notification.setSoundName_("NSUserNotificationDefaultSoundName")
        notification.setDeliveryDate_(Foundation.NSDate.dateWithTimeInterval_sinceDate_(0, Foundation.NSDate.date()))
        NSUserNotificationCenter.defaultUserNotificationCenter().scheduleNotification_(notification)

if '-v' in sys.argv or '--version' in sys.argv:
    # do this here before any of the config checks are run
    alert('d3ploy %s' % VERSION, os.EX_OK, DEFAULT_COLOR)
    

valid_acls      =   ["private", "public-read", "public-read-write", "authenticated-read"]

parser          =   argparse.ArgumentParser()
parser.add_argument('environment', help = "Which environment to deploy to", nargs = "?", type = str, default = "default")
parser.add_argument('-a', '--access-key', help = "AWS Access Key ID", type = str)
parser.add_argument('-s', '--access-secret', help = "AWS Access Key Secret", type = str)
parser.add_argument('-f', '--force', help = "Upload all files whether they are currently up to date on S3 or not", action = "store_true", default = False)
parser.add_argument('--delete', help = "Remove orphaned files from S3", action = "store_true", default = False)
parser.add_argument('--all', help = "Upload to all environments", action = "store_true", default = False)
parser.add_argument('-n', '--dry-run', help = "Show which files would be updated without uploading to S3", action = "store_true", default = False)
parser.add_argument('--acl', help = "The ACL to apply to uploaded files.", type = str, default = "public-read", choices = valid_acls)
parser.add_argument('-v', '--version', help = "Print the script version and exit", action = "store_true", default = False)
parser.add_argument('-z', '--gzip', help = "gzip files before uploading", action = "store_true", default = False)
parser.add_argument('--confirm', help = "Confirm each file before deleting. Only works when --delete is set.", action = "store_true", default = False)
parser.add_argument('--charset', help = "The charset header to add to text files", default = False)
parser.add_argument('-c', '--config', help = "path to config file. Defaults to deploy.json in current directory", type = str, default = "deploy.json")
args            =   parser.parse_args()

# load the config file
try:
    config      =   open(args.config, 'r')
except IOError:
    alert("config file is missing. Default is deploy.json in your current directory. See http://dryan.github.io/d3ploy for more information.", os.EX_NOINPUT)

config          =   json.load(config)

environments    =   [str(item) for item in config.keys()]

#Check if no environments are configured in the file
if not environments:
    alert("No environments found in config file: %s", os.EX_NOINPUT)

#check if environment actually exists in the config file
if args.environment not in environments:
    valid_envs = '(%s)' % ', '.join(map(str, environments))
    alert("environment %s not found in config. Choose from '%s'" %(args.environment, valid_envs), os.EX_NOINPUT)
    

AWS_KEY         =   args.access_key
AWS_SECRET      =   args.access_secret

# look for credentials file in this directory
if os.path.exists('.aws'):
    local_config    =   ConfigParser.ConfigParser()
    local_config.read('.aws')
    if local_config.has_section('Credentials'):
        if AWS_KEY is None:
            AWS_KEY     =   local_config.get('Credentials', 'aws_access_key_id')
        if AWS_SECRET is None:
            AWS_SECRET  =   local_config.get('Credentials', 'aws_secret_access_key')

# lookup global AWS keys if needed
if AWS_KEY is None:
    AWS_KEY     =   boto.config.get('Credentials', 'aws_access_key_id')
    
if AWS_SECRET is None:
    AWS_SECRET  =   boto.config.get('Credentials', 'aws_secret_access_key')
    
# lookup AWS key environment variables
if AWS_KEY is None:
    AWS_KEY     =   os.environ.get('AWS_ACCESS_KEY_ID')
if AWS_SECRET is None:
    AWS_SECRET  =   os.environ.get('AWS_SECRET_ACCESS_KEY')
    
def upload_files(env, config):
    alert('Using settings for "%s" environment' % env)
    
    bucket              =   config.get('bucket')
    if not bucket:
        alert('A bucket to upload to was not specified for "%s" environment' % args.environment, os.EX_NOINPUT)

    KEY         =   config.get('aws_key', AWS_KEY)

    SECRET      =   config.get('aws_secret', AWS_SECRET)
    
    if KEY is None or SECRET is None:
        alert("AWS credentials were not found. See https://gist.github.com/dryan/5317321 for more information.", os.EX_NOINPUT)
    
    s3connection        =   boto.connect_s3(KEY, SECRET)

    # test the bucket connection
    try:
        s3bucket        =   s3connection.get_bucket(bucket)
    except boto.exception.S3ResponseError:
        alert('Bucket "%s" could not be retrieved with the specified credentials' % bucket, os.EX_NOINPUT)

    # get the rest of the options
    local_path          =   config.get('local_path', '.')
    bucket_path         =   config.get('bucket_path', '/')
    excludes            =   config.get('exclude', [])
    svc_directories     =   ['.git', '.svn']

    if type(excludes) == str or type(excludes) == unicode:
        excludes        =   [excludes]
    
    exclude_regexes     =   [re.compile(r'%s' % s) for s in excludes]

    files               =   []

    for dirname, dirnames, filenames in os.walk(local_path):
        for filename in filenames:
            filename    =   os.path.join(dirname, filename)
            excluded    =   False
            for regex in exclude_regexes:
                if regex.search(filename):
                    excluded    =   True
            if not excluded:
                files.append(filename)
        
        for svc_directory in svc_directories:
            if svc_directory in dirnames:
                dirnames.remove(svc_directory)
            
    prefix_regex        =   re.compile(r'^%s' % local_path)

    keynames            =   []
    updated             =   0
    deleted             =   0
    caches              =   config.get('cache', {})

    for filename in files:
        keyname         =   '/'.join([bucket_path.rstrip('/'), prefix_regex.sub('', filename).lstrip('/')])
        keynames.append(keyname.lstrip('/'))
        s3key           =   s3bucket.get_key(keyname)
        local_file      =   open(filename, 'r')
        md5             =   boto.utils.compute_md5(local_file)[0] # this needs to be computed before gzipping
        local_file.close()

        if args.gzip or config.get('gzip', False):
            if not mimetypes.guess_type(filename)[1] == 'gzip':
                f_in    =   open(filename, 'rb')
                f_out   =   gzip.open(filename + '.gz', 'wb')
                f_out.writelines(f_in)
                f_out.close()
                f_in.close()
                filename    =   f_out.name
        local_file      =   open(filename, 'r')
        is_gzipped      =   local_file.read().find('\x1f\x8b') == 0
        local_file.seek(0)
        if s3key is None or args.force or not s3key.get_metadata('d3ploy-hash') == md5:
            alert('Copying %s to %s%s' % (filename, bucket, keyname))
            updated     +=  1
            if args.dry_run:
                if not filename in files:
                    # this filename was modified by gzipping
                    os.remove(filename)
                continue
            if s3key is None:
                s3key   =   s3bucket.new_key(keyname)
            headers     =   {}
            mimetype    =   mimetypes.guess_type(filename)
            if is_gzipped or mimetype[1] == 'gzip':
                headers['Content-Encoding'] =   'gzip'
            if args.charset or config.get('charset', False) and mimetype[0] and mimetype[0].split('/')[0] == 'text':
                headers['Content-Type']     =   str('%s;charset=%s' % (mimetype[0], args.charset or config.get('charset')))
            if mimetype[0] in caches.keys():
                s3key.set_metadata('Cache-Control', str('max-age=%s, public' % str(caches.get(mimetype[0]))))
            s3key.set_metadata('d3ploy-hash', md5)
            s3key.set_contents_from_file(local_file, headers = headers)
            s3key.set_acl(args.acl)
        if not filename in files:
            # this filename was modified by gzipping
            os.remove(filename)
        local_file.close()

    if args.delete or config.get('delete', False):
        for key in s3bucket.list(prefix = bucket_path.lstrip('/')):
            if not key.name in keynames:
                if args.confirm or config.get('confirm', False):
                    confirmed   =   raw_input('Remove %s/%s [yN]: ' % (bucket, key.name.lstrip('/'))) in ["Y", "y"]
                else:
                    confirmed   =   True
                if confirmed:
                    alert('Deleting %s/%s' % (bucket, key.name.lstrip('/')))
                    deleted     +=  1
                    if args.dry_run:
                        continue
                    key.delete()
                else:
                    alert('Skipping removal of %s/%s' % (bucket, key.name.lstrip('/')))
        
    verb    =   "would be" if args.dry_run else "were"
    notify(args.environment, "%d files %s updated" % (updated, verb))
    if args.delete or config.get('delete', False):
        notify(args.environment, "%d files %s removed" % (deleted, verb))
    alert("")

if not args.environment in config:
    alert('The "%s" environment was not found in deploy.json' % args.environment, os.EX_NOINPUT)

def main():
    if args.all:
        for environ in config:
            alert("Uploading environment %d of %d" % (config.keys().index(environ) + 1, len(config.keys())))
            environ_config  =   config[environ]
            if not environ == "default":
                environ_config  =   dict(config['default'].items() + config[environ].items())
            upload_files(environ, environ_config)
    else:
        environ_config  =   config[args.environment]
        if not args.environment == "default":
            environ_config  =   dict(config['default'].items() + config[args.environment].items())
        upload_files(args.environment, environ_config)

if __name__ == "__main__":
    main()
