#!/usr/bin/env python3


# iLert Zabbix Plugin
#
# Copyright (c) 2020, iLert GmbH. <support@ilert.com>
# All rights reserved.
# see https://docs.ilert.com/integrations/zabbix for setup instructions


import fcntl
import json
import os
import syslog
import uuid

PLUGIN_VERSION = "1.0"

CONFIG = {
    'tmp_dir': '/tmp/ilert_zabbix',
    'api_endpoint': 'https://api.ilert.com',
    'api_port': 443
}


def persist_event(directory, api_key, event_type, payload):
    """Persists event to disk"""
    syslog.syslog('writing event to disk...')

    json_doc = create_json(api_key, event_type, payload)

    uid = uuid.uuid4()

    filename = "%s.ilert" % uid
    filename_tmp = "%s.tmp" % uid
    file_path = "%s/%s" % (directory, filename)
    file_path_tmp = "%s/%s" % (directory, filename_tmp)

    try:
        # atomic write using tmp file, see http://stackoverflow.com/questions/2333872
        with open(file_path_tmp, "w") as f:
            f.write(json_doc)
            # make sure all data is on disk
            f.flush()
            # skip os.sync in favor of performance/responsiveness
            # os.fsync(f.fileno())
            f.close()
            os.rename(file_path_tmp, file_path)
            syslog.syslog('created event file in %s' % file_path)
    except Exception as e:
        syslog.syslog(syslog.LOG_ERR, "could not write event to %s. Cause: %s %s" % (file_path, type(e), e.args))
        exit(1)


def create_json(api_key, event_type, payload):
    """Create event json using the provided api key, event type and event payload"""
    json_dict = dict(api_key=api_key, event_type=event_type, payload=payload)
    return json.dumps(json_dict)


def lock_and_flush(directory, endpoint, port):
    """Lock event directory and call flush"""
    lock_filename = "%s/lockfile" % directory

    lockfile = open(lock_filename, "w")

    try:
        fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
        flush(directory, endpoint, port)
    finally:
        lockfile.close()


def flush(directory, endpoint, port):
    """Send all events in event directory to iLert"""
    import urllib.request
    from urllib.error import HTTPError
    from urllib.error import URLError

    headers = {"Content-type": "application/json", "Accept": "application/json"}
    url = "%s:%s/api/v1/events/zabbix" % (endpoint, port)

    # populate list of event files sorted by creation date
    events = [os.path.join(directory, f) for f in os.listdir(directory)]
    events = list(filter(lambda x: x.endswith(".ilert"), events))
    events.sort(key=lambda x: os.path.getmtime(x))

    for event in events:
        try:
            with open(event, 'r') as f:
                json_doc = json.load(f)
        except IOError:
            continue

        syslog.syslog('sending event %s to iLert...' % event)

        try:
            url_with_api_key = url + "/" + json_doc['api_key']
            req = urllib.request.Request(url_with_api_key, json.dumps(json_doc).encode("utf-8"), headers)
            urllib.request.urlopen(req, timeout=60)
        except HTTPError as e:
            if e.code == 429:
                syslog.syslog(syslog.LOG_WARNING, "too many requests, will try later. Server response: %s" % e.read())
            elif 400 <= e.code <= 499:
                syslog.syslog(syslog.LOG_WARNING, "event not accepted by iLert. Reason: %s" % e.read())
                os.remove(event)
            else:
                syslog.syslog(syslog.LOG_ERR,
                              "could not send event to iLert. HTTP error code %s, reason: %s, %s" % (
                                  e.code, e.reason, e.read()))
        except URLError as e:
            syslog.syslog(syslog.LOG_ERR, "could not send event to iLert. Reason: %s" % e.reason)
        except Exception as e:
            syslog.syslog(syslog.LOG_ERR,
                          "an unexpected error occurred. Please report a bug. Cause: %s %s" % (type(e), e.args))
        else:
            os.remove(event)
            syslog.syslog('event %s has been sent to iLert and removed from event directory' % event)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='send events from Zabbix to iLert')

    parser.add_argument(
        '-m', '--mode',
        choices=['save', 'send'],
        default='save',
        help='Execution mode: "save" persists an event to disk and "send" submits all saved events '
             'to iLert. Note that after every "save" "send" will also be called. Default: %(default)s'
    )

    parser.add_argument('api_key', nargs='?', help='API key for the alert source in iLert')
    parser.add_argument('event_type', nargs='?', choices=['alert', 'ack', 'resolve'], help='event type')
    parser.add_argument('payload', nargs='?', help='Zabbix message body')
    parser.add_argument('--version', action='version', version=PLUGIN_VERSION)
    args = parser.parse_args()

    if not os.path.exists(CONFIG['tmp_dir']):
        os.makedirs(CONFIG['tmp_dir'])

    if args.mode == "save":
        if args.api_key is None:
            error_msg = "positional argument api_key is required in save mode"
            syslog.syslog(syslog.LOG_ERR, error_msg)
            parser.error(error_msg)

        if args.event_type is None:
            error_msg = "positional argument event_type is required in save mode"
            syslog.syslog(syslog.LOG_ERR, error_msg)
            parser.error(error_msg)

        if args.payload is None:
            error_msg = "positional argument payload is required in save mode"
            syslog.syslog(syslog.LOG_ERR, error_msg)
            parser.error(error_msg)

        # validate json first
        try:
            json_payload = json.loads(args.payload.strip())
        except ValueError as e:
            error_msg = "payload must be valid json (see https://docs.ilert.com/integrations/zabbix). " \
                        "Error: %s " % e.args
            syslog.syslog(syslog.LOG_ERR, error_msg)
            parser.error(error_msg)

        # populate payload data
        payload = dict(json_payload)
        payload.update(PLUGIN_VERSION=PLUGIN_VERSION)
        persist_event(CONFIG['tmp_dir'], args.api_key, args.event_type, payload)
        lock_and_flush(CONFIG['tmp_dir'], CONFIG['api_endpoint'], CONFIG['api_port'])
    elif args.mode == "send":
        lock_and_flush(CONFIG['tmp_dir'], CONFIG['api_endpoint'], CONFIG['api_port'])

    exit(0)


if __name__ == '__main__':
    main()
