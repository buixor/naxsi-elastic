naxsi-elastic
=============

naxsi-elastic is a simple tool that allows you to transform/inject naxsi's log into elasticsearch, allowing you to :
  * Use kibana for visualisation
  * Use ES for its search power
  * Be one of the cool kids


Log lines can be received from various ways :
  * Log files : multiple logfiles can be provided, and it supports globbing
  * Syslog : naxsi-elastic can create & read from named pipes, convenient to plug it directly to rsyslog

Examples :
  * head -n 100 bla.log | python es_import.py -f "" # read log lines from STDIN
  * python es_import.py -f "foo/bar/*/foo*.log"
  * python es_import.py -c /tmp/MOAR  # read from named pipe, to be used with rsyslog

Please report any encountered bugs !





