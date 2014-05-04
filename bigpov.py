#!/usr/bin/python

import Image, time, fcntl, array, sys, os, random
import urllib, cStringIO, errno
import RPi.GPIO as gpio
import cPickle as pickle
import threading
import atexit
import datetime

# Configurable values
fromTop       = False
dev           = "/dev/spidev0.0"
skip          = 0
skipAfter     = 32
length        = 92 #106 #218 #326
displaytime   = 5
redButton     = 14
redButtonFile = "rose.jpg"
correctStart  = 5000
obey_shutdown = False

light_start = datetime.time(4)
light_end = datetime.time(20)

# Setup GPIO pins
gpio.setmode(gpio.BCM)
gpio.setwarnings(False)
gpio.setup(redButton, gpio.IN, pull_up_down=gpio.PUD_UP)

# Initialize GPIO pins and interrupts
gpio.add_event_detect(redButton, gpio.FALLING, bouncetime=1000)

# Open SPI device
spidev    = file(dev, "wb")
# XXX: hard-coded ioctl constant for SPI_IOC_WR_MAX_SPEED_HZ
fcntl.ioctl(spidev, 0x40046b04, array.array('L', [16000000]))

# Calculate gamma correction table.  This includes
# LPD8806-specific conversion (7-bit color w/high bit set).
gamma = bytearray(256)
gbCorrectGamma = bytearray(256)
for i in range(256):
	gamma[i] = 0x80 | int(pow(float(i) / 255.0, 2.5) * 127.0 + 0.5)
	gbCorrectGamma[i] = 0x80 | int(pow(float(i) / 280.0, 2.5) * 127.0 + 0.5)

clearBytes = bytearray([0x80] * (skip + length) * 3 + [0] * 30)

imgpath = os.path.join(os.path.dirname(__file__), 'images')
cachepath = os.path.join(os.path.dirname(__file__), 'cache')
try:
  os.makedirs(cachepath)
except OSError as exc:
  if exc.errno == errno.EEXIST and os.path.isdir(cachepath):
    pass
  else: raise

l = threading.Lock()
targettime = time.time()
display_columns = None
display_repeating = False
display_updated = False
quitting = False

def show_instagram():
  try:
    #print "showing instagram?"
    url = urllib.urlopen("http://selfobserved.org/instagram.php")
    if url.getcode() != 200:
      #print "  no image this time."
      return False
    filename = "_from_insta_" + url.geturl().split('/')[-1]
    #print "  got", url.geturl()
    filepath = os.path.join(imgpath, filename)
    if os.path.isfile(filepath):
      os.remove(filepath)
      #print "  but we already have it!"
      return False
    f = open(filepath, 'w')
    f.write(url.read())
    f.close()
    #print "  showing it"
    displayFile(filename)
    return True
  except IOError:
    return False

def displayFile(file):
  global targettime
  global display_columns
  global display_repeating
  global display_updated
  #file = 'povtest.png'
  repeating = "gggtiled" in file
  try:
    #print "this one is %s" % file
    cacheName = os.path.join(cachepath, str(length) + '_' + str(int(fromTop)) + '_' + str(skip) + '_' + str(skipAfter) + '.' + file)
    try:
      cacheFile = open(cacheName, 'rb')
      columns = pickle.load(cacheFile)
      cacheFile.close()
      #width = len(columns)
      #print "  cache load of %s worked!" % cacheName
    except:
      #print '  loading manually'
      img = Image.open(os.path.join(imgpath, file))
      if img.size[1] > length or img.size[1] < length * 0.9:
        newsize = tuple([int(float(s) / img.size[1] * length) for s in img.size])
        #print "  size is %dx%d; resizing to %dx%d!" % (img.size + newsize)
        img = img.resize(newsize, Image.ANTIALIAS)
      pixels = img.convert("RGB").load()
      width = img.size[0]
      height = img.size[1]

      columns = [0 for x in range(width)]
      for x in range(width):
        columns[x] = bytearray([0x80] * (skip + length + skipAfter) * 3 + [0] * 30)
        #columns[x] = bytearray([0x80] * skip * 3 + [0] * (length * 3 + 30) + [0x80] * skipAfter * 3)
        for y in range(height):
          value = pixels[x, y]
          colY = y if fromTop else height - y - 1
          columns[x][skip * 3 + colY * 3 + 1] = gamma[value[0]]
          if (skip + colY) < correctStart:
            columns[x][skip * 3 + colY * 3 + 2] = gamma[value[1]]
            columns[x][skip * 3 + colY * 3 + 0] = gamma[value[2]]
          else:
            columns[x][skip * 3 + colY * 3 + 2] = gbCorrectGamma[value[1]]
            columns[x][skip * 3 + colY * 3 + 0] = gbCorrectGamma[value[2]]

      cacheFile = open(cacheName, 'wb')
      pickle.dump(columns, cacheFile, pickle.HIGHEST_PROTOCOL)
      cacheFile.close()
      #print '  successfully saved cache', cacheName

    # Wait for current image to have been displayed for targettime
    while time.time() < targettime and not quitting:
      time.sleep(0.1)
    l.acquire()
    display_columns = columns
    display_repeating = repeating
    display_updated = True
    l.release()
    # Set new targettime
    targettime = time.time() + displaytime

  except IOError:
    print "%s is not a valid image" % file

def display_worker():
  global display_updated
  while True:
    l.acquire()
    columns = display_columns
    repeating = display_repeating
    l.release()
    if columns is None:
      spidev.write(clearBytes)
      spidev.flush()
      time.sleep(0.1)
      continue
    width = len(columns)
    while not display_updated:
      for x in range(width):
        spidev.write(columns[x])
        spidev.flush()
      if repeating:
        continue
      for x in range(width):
        spidev.write(columns[width - x - 1])
        spidev.flush()
    display_updated = False
    if quitting:
      break

def shutdown():
  global display_updated
  global quitting
  quitting = True
  display_updated = True
  dw.join()
  spidev.write(clearBytes)
  spidev.flush()
  print "\nbye"
  sys.exit(0)

def check_shutdown():
  if obey_shutdown and time.time() >= shutdownTime:
    shutdown()

# MAIN LOOP

dw = threading.Thread(target=display_worker)
dw.daemon = True
dw.start()

try:
  # 10 hours between civil twilights, and give us 15 minutes before power off.
  shutdownTime = time.time() + 60 * (60 * 10 - 15)

  while True:

    while light_start < datetime.datetime.now().time() < light_end:
      l.acquire()
      display_columns = None
      display_updated = True
      l.release()
      time.sleep(10)

    # Iterate over files in [this file]/images/
    files = [ f for f in os.listdir(imgpath)
      if os.path.isfile(os.path.join(imgpath,f)) ]
    random.shuffle(files)

    for file in files:
      displayFile(file)
      # Shutdown if we should
      check_shutdown()
      # Show Instagram images while we can
      while show_instagram():
        check_shutdown()

except KeyboardInterrupt:
  shutdown()


'''
clearBytes = bytearray([0x80] * (skip + length) * 3 + [0] * 10)
whiteBytes = bytearray([0x80] * (skip + length) * 3 + [0] * 10)
red = 80
green = 255
blue = 127
for i in range(183, 234):
  whiteBytes[i * 3 + 1] = redCorrectGamma[red];
  whiteBytes[i * 3 + 2] = gamma[green];
  whiteBytes[i * 3 + 0] = gamma[blue];
for i in range(234, 285):
  whiteBytes[i * 3 + 1] = gamma[red];
  whiteBytes[i * 3 + 2] = gbCorrectGamma[green];
  whiteBytes[i * 3 + 0] = gbCorrectGamma[blue];
spidev.write(clearBytes)
spidev.flush()
spidev.write(whiteBytes)
spidev.flush()
sys.exit(0)
'''
