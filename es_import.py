from nxparse import *
from optparse import OptionParser
import array, fcntl, struct, termios
import os
import datetime

F_SETPIPE_SZ = 1031  # Linux 2.6.35+
F_GETPIPE_SZ = 1032  # Linux 2.6.35+


def open_fifo(fifo):
    try:
        os.mkfifo(fifo)
    except OSError:
        print "Fifo ["+fifo+"] already exists (non fatal)."
    except Exception, e:
        print "Unable to create fifo ["+fifo+"]"
    try:
        print "Opening fifo ... will return when data is available."
        fifo_fd = open(fifo, 'r')
        fcntl.fcntl(fifo_fd, F_SETPIPE_SZ, 1000000)
        print "Pipe (modified) size : "+str(fcntl.fcntl(fifo_fd, F_GETPIPE_SZ))
    except Exception, e:
        print "Unable to create fifo, error: "+str(e)
        return None
    return fifo_fd

def macquire(line):
    z = parser.parse_raw_line(line)
    # add data str and coords
    if z is not None:
        for event in z['events']:
            event['date'] = z['date']
            event['coord'] = geoloc.ip2ll(event['ip'])
        # print "Got data :)"
        # pprint.pprint(z)
        print ".",
        injector.insert(z)
    else:
        pass
        #print "No data ? "+line
    #print ""


usage = """%prog -f <files> -c <fifo> [-i]"""
parser = OptionParser(usage=usage)
parser.add_option('-c', '--create-fifo', type="string", dest="fifo", help="Create & read from FIFO (for syslog feed). Exclusive with -f.")
parser.add_option('-f', '--files', type="string", dest="files", help="Files to inject to ES")
parser.add_option('-i', '--infinite', dest="infinite", action="store_true", help="When no files are provided,"
                  "es_import will read from stdin. Infinite avoids es_import to stop if not input occured within 5 secondes.")

(options, args) = parser.parse_args()

if options.files is None and options.fifo is None:
    parser.print_help()
    sys.exit(-1)
if options.files is not None and options.fifo is not None:
    print "FIFO and file read are exclusive."
    sys.exit(-1)


injector = ESInject("localhost:9200", "nxapi")
parser = NxParser()
parser.out_date_format = "%Y-%m-%dT%H:%M:%SZ"
geoloc = NxGeoLoc()
if options.files is not None and options.files == "":
    if options.infinite is True:
        #print "reading from stdin (infinite)"
        reader = NxReader(macquire, lglob=[], stdin=True, stdin_timeout=None)
    else:
        #print "reading from stdin (with timeout)"
        reader = NxReader(macquire, lglob=[], stdin=True)
    reader.read_files()
elif options.files is not None and len(options.files) > 0:
    #print "reading from files !"+options.files+"!"
    reader = NxReader(macquire, lglob=[options.files])
    reader.read_files()
elif options.fifo is not None:
    fd = open_fifo(options.fifo)
    if options.infinite is True:
        #print "infinite!!"
        reader = NxReader(macquire, fd=fd, stdin_timeout=None)
    else:
        reader = NxReader(macquire, fd=fd)
        
    while True:
        print "Start read at "+str(datetime.datetime.now())
        reader.read_files()
        print "Stopped read at "+str(datetime.datetime.now())

injector.stop()


