# Parses a line of log, and potentially returns a dict of dict.
import sys
import pprint
import time
import glob
import logging
import string
import urlparse
import itertools
import gzip
import bz2
from select import select

import urllib2 as urllib
import json
        
class NxReader():
    """ Feeds the given injector from logfiles """
    def __init__(self, acquire_fct, stdin=False, lglob=[], fd=None,
                 stdin_timeout=5):
        self.acquire_fct = acquire_fct
        self.files = []
        self.timeout = stdin_timeout
        self.stdin = False
        self.fd = fd
        if stdin is not False:
            logging.warning("Using stdin")
            self.stdin = True
            return
        if len(lglob) > 0:
            for regex in lglob:
                self.files.extend(glob.glob(regex))
            logging.warning("List of files :"+str(self.files))
        if self.fd is not None:
            logging.warning("Reading from supplied FD (fifo ?)")
    def read_fd(self, fd):
        if self.timeout is not None:
#            print "timeout .."
            rlist, _, _ = select([fd], [], [], self.timeout)
        else:
#            print "no timeout .."
            rlist, _, _ = select([fd], [], [])
        success = discard = not_nx = malformed = 0
        if rlist:
            s = fd.readline()
            if s == '':
#                print "empty .."
                #return True
                return False
            self.acquire_fct(s)
            return True
        else:
            return False
    def read_files(self):
        if self.stdin is True:
            ret = ""
            while self.read_fd(sys.stdin) is True:
                pass
            return 0
        if self.fd is not None:
            ret = ""
            while self.read_fd(self.fd) is True:
                pass
            return 0
        count = 0
        total = 0
        for lfile in self.files:
            success = not_nx = discard = malformed = fragmented = reunited = 0
            logging.info("Importing file "+lfile)
            try:
                if lfile.endswith(".gz"):
                    print "GZ open"
                    fd = gzip.open(lfile, "rb")
                elif lfile.endswith(".bz2"):
                    print "BZ2 open"
                    fd = bz2.BZ2File(lfile, "r")
                else:
                    print "log open"
                    fd = open(lfile, "r")
            except:
                logging.critical("Unable to open file : "+lfile)
                return 1
            for line in fd:
                self.acquire_fct(line)
            fd.close()
        return 0


class NxParser():
    def __init__(self):
        # output date format
        self.out_date_format = "%Y/%m/%d %H:%M:%S"
        # Start of Data / End of data marker
        self.sod_marker = [' [error] ', ' [debug] ']
        self.eod_marker = [', client: ', '']
        # naxsi data keywords
        self.naxsi_keywords = [" NAXSI_FMT: ", " NAXSI_EXLOG: "]
        # keep track of fragmented lines (seed_start=X seed_end=X)
        self.reunited_lines = 0
        self.fragmented_lines = 0
        self.multiline_buf = {}
        # store generated objects
        self.dict_buf = []

    def unify_date(self, date):
        """ tries to parse a text date, 
        returns date object or None on error """
        idx = 0
        res = ""
        supported_formats = [
            "%b  %d %H:%M:%S",
            "%b %d %H:%M:%S",
            "%Y/%m/%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S"
#            "%Y-%m-%dT%H:%M:%S+%:z"
            ]
        while date[idx] == " " or date[idx] == "\t":
            idx += 1
        success = 0
        for date_format in supported_formats:
            nb_sp = date_format.count(" ")
            clean_date = string.join(date.split(" ")[:nb_sp+1], " ")
            # strptime does not support numeric time zone, hack.
            idx = clean_date.find("+")
            if idx != -1:
                clean_date = clean_date[:idx]
            try:
                x = time.strptime(clean_date, date_format)
                z = time.strftime(self.out_date_format, x)
                success = 1
                break
            except:
                #print "'"+clean_date+"' not in format '"+date_format+"'"
                pass
        if success == 0:
            logging.critical("Unable to parse date format :'"+date+"'")
            return None
        return z

    # returns line, ready for parsing.
    # returns none if line contains no naxsi data
    def clean_line(self, line):
        """ returns an array of [date, "NAXSI_..."] from a 
        raw log line. 2nd item starts at first naxsi keyword
        found. """
        ret = [None, None]
        # Don't try to parse if no naxsi keyword is found
        for word in self.naxsi_keywords:
            idx = line.find(word)
            if idx != -1:
                break
        if idx == -1:
            return None
            
        line = line.rstrip('\n')
        for mark in self.sod_marker:
            date_end = line.find(mark)
            if date_end != -1:
                break
        for mark in self.eod_marker:
            if mark == '':
                data_end = len(line)
                break
            data_end = line.find(mark)
            if data_end != -1:
                break
        if date_end == -1 or data_end == 1:
            self.bad_line += 1
            return None
        ret[0] = self.unify_date(line[:date_end])
        chunk = line[date_end:data_end]
        md = None
        for word in self.naxsi_keywords:
            idx = chunk.find(word)
            if (idx != -1):
                ret[1] = chunk[idx+len(word):]
        if ret[1] is None:
            self.bad_line += 1
            return None
        return ret
    # attempts to clean and parse a line
    def parse_raw_line(self, line):
        clean_dict = self.clean_line(line)
        if clean_dict is None:
            logging.debug("not a naxsi line")
            return None
        nlist = self.parse_line(clean_dict[1])
        if nlist is None:
            return None
        return {'date' : clean_dict[0], 'events' : nlist}
    def parse_line(self, line):
        ndict = self.tokenize_log(line)
        if ndict is None:
            logging.critical("Unable to tokenize line "+line)
            return None
        nlist = self.demult_exception(ndict)
        return nlist
    def demult_exception(self, event):
        demult = []
        import copy
        if event.get('seed_start') and event.get('seed_end') is None:
            #First line of a multiline naxsi fmt
            self.multiline_buf[event['seed_start']] = event
            self.fragmented_lines += 1
            return demult
        elif event.get('seed_start') and event.get('seed_end'):
            # naxsi fmt is very long, at least 3 lines
            self.fragmented_lines += 1
            if self.multiline_buf.get(event['seed_end']) is None:
                logging.critical("Orphans end {0} / start {1}".format(event['seed_end'], 
                                                                      event['seed_start']))
                return demult
            self.multiline_buf[event['seed_end']].update(event)
            self.multiline_buf[event['seed_start']] = self.multiline_buf[event['seed_end']]
            del self.multiline_buf[event['seed_end']]
            return demult
        elif event.get('seed_start') is None and event.get('seed_end'):
            # last line of the naxsi_fmt, just update the dict, and parse it like a normal line
            if self.multiline_buf.get(event['seed_end']) is None:
                logging.critical('Got a line with seed_end {0}, but i cant find a matching seed_start...\nLine will probably be incomplete'.format(event['seed_end']))
                return demult
            self.fragmented_lines += 1
            self.reunited_lines += 1
            self.multiline_buf[event['seed_end']].update(event)
            event = self.multiline_buf[event['seed_end']]
            del self.multiline_buf[event['seed_end']]
        entry = {}
        
        for x in ['uri', 'server', 'content', 'ip', 'date', 'var_name']:
            entry[x] = event.get(x, '')
        clean = entry
        
        # NAXSI_EXLOG lines only have one triple (zone,id,var_name), but has non-empty content
        if 'zone' in event.keys():
            if 'var_name' in event.keys():
                entry['var_name'] = event['var_name']
            entry['zone'] = event['zone']
            entry['id'] = event['id']
            demult.append(entry)
            return demult
        
        # NAXSI_FMT can have many (zone,id,var_name), but does not have content
        # we iterate over triples.
        elif 'zone0' in event.keys():
            commit = True
            for i in itertools.count():
                entry = copy.deepcopy(clean)
                zn = ''
                vn = ''
                rn = ''
                if 'var_name' + str(i) in event.keys():
                    entry['var_name'] = event['var_name' + str(i)]
                if 'zone' + str(i) in event.keys():
                    entry['zone']  = event['zone' + str(i)]
                else:
                    commit = False
                    break
                if 'id' + str(i) in event.keys():
                    entry['id'] = event['id' + str(i)]
                else:
                    commit = False
                    break
                if commit is True:
                    demult.append(entry)
                else:
                    logging.warning("Malformed/incomplete event [missing subfield]")
                    logging.info(pprint.pformat(event))
                    return demult
            return demult
        else:
            logging.warning("Malformed/incomplete event [no zone]")
            logging.info(pprint.pformat(event))
            return demult

    def tokenize_log(self, line):
        """Parses a naxsi exception to a dict, 
        1 on error, 0 on success"""
        odict = urlparse.parse_qs(line)
        # one value per key, reduce.
        for x in odict.keys():
            odict[x][0] = odict[x][0].replace('\n', "\\n")
            odict[x][0] = odict[x][0].replace('\r', "\\r")
            odict[x] = odict[x][0]
        # check for incomplete/truncated lines
        if 'zone0' in odict.keys():
            for i in itertools.count():
                is_z = is_id = False
                if 'zone' + str(i) in odict.keys():
                    is_z = True
                if 'id' + str(i) in odict.keys():
                    is_id = True
                if is_z is True and is_id is True:
                    continue
                if is_z is False and is_id is False:
                    break
                # clean our mess if we have to.
                try:
                    del (odict['zone' + str(i)])
                    del (odict['id' + str(i)])
                    del (odict['var_name' + str(i)])
                except:
                    pass
                break
        return odict

  
class NxInjector():
    def __init__(self, auto_commit_limit=10):
        self.nlist = []
        self.auto_commit = auto_commit_limit
        self.total_objs = 0
        self.total_commits = 0
    # optional
    def get_ready(self):
        pass
    def insert(self, obj):
        self.nlist.append(obj)
        if self.auto_commit > 0 and len(self.nlist) > self.auto_commit:
            return self.commit()
        return True
    def commit(self):
        return False
    def stop(self):
        self.commit()
        pass


class ESInject(NxInjector):
    def __init__(self, host, index):
#        super(ESInject, self).__init__(value=20)
        NxInjector.__init__(self)
        self.host = host
        self.index = index
        self.set_mappings()
    def esreq(self, pidx_uri, data, method="PUT"):
        try:
            body = json.dumps(data)
        except:
            print "Unable to dumps data."
            return False
        try:
            req = urllib.Request("http://"+self.host+"/"+self.index+pidx_uri, data=body)
            f = urllib.urlopen(req)
            resp = f.read()
            f.close()
        except:
            logging.critical("Unable to emit request.")
            return False
        return True
    def set_mappings(self):
        # create index
        #self.esreq("", None)
        # create mapping
        import os
        mappings = { "events" : { "properties" : { "var_name" : {"type": "string", "index" : "not_analyzed"}, "uri" : {"type": "string", "index" : "not_analyzed"},"zone" : {"type": "string", "index" : "not_analyzed"},  "server" : {"type": "string", "index" : "not_analyzed"}}}}
        self.esreq("/events/_mapping", mappings)
    def commit(self):
        """Process list of dict (yes) and push them to DB """
        self.total_objs += len(self.nlist)
        count = 0
        for evt_array in self.nlist:
            for entry in evt_array['events']:
                try:
                    body = json.dumps(entry)
#                    print "b:"+body
                    req = urllib.Request("http://"+self.host+"/"+self.index+"/events", data=body)
                    f = urllib.urlopen(req)
                    resp = f.read()
                    f.close()
                    count += 1
                except:
                    logging.critical("Unable to encode event :"+pprint.pformat(entry))
                
        # req = urllib.Request("http://"+self.host+"/"+self.index+"/events/_bulk?pretty=true", data=body)
        # f = urllib.urlopen(req)
        # resp = f.read()
        # f.close()
        # logging.debug("ES rep:"+resp)
        self.total_commits += count
        logging.debug("Written "+str(self.total_commits)+" events")
        print "Written "+str(self.total_commits)+" events"
        del self.nlist[0:len(self.nlist)]
        

class NxGeoLoc():
    def __init__(self):
        try:
            import GeoIP
            self.gi = GeoIP.new(GeoIP.GEOIP_MEMORY_CACHE)
        except:
            logging.warning("""Python's GeoIP module is not present.
            'World Map' reports won't work,
            and you can't use per-country filters.""")
    def cc2ll(self, country):
        coord = [37.090240,-95.7128910]
        #coord = "37.090240,-95.7128910"
        try:
            fd = open("country2coords.txt", "r")
        except:
            return "Unable to open GeoLoc database, please check your setup."
        fd.seek(0)
        for cn in fd:
            if cn.startswith(country+":"):
                x = cn[len(country)+1:-1]
                ar = x.split(',')
                coord[0] = float(ar[1])
                coord[1] = float(ar[0])
                #pprint.pprint(coord)
#                coord =  cn[len(country)+1:-1]
                break
        return coord
    def ip2cc(self, ip):
        country = self.gi.country_code_by_addr(ip)
        # pun intended
        if country is None or len(country) < 2:
            country = "CN"
        return country
    def ip2ll(self, ip):
        return self.cc2ll(self.ip2cc(ip))
