#!/usr/bin/python
from __future__ import absolute_import, division, print_function, unicode_literals
''' Simplified slideshow system using ImageSprite and without threading for background
loading of images (so may show delay for v large images).
    Also has a minimal use of PointText and TextBlock system with reduced  codepoints
and reduced grid_size to give better resolution for large characters.
    Also shows a simple use of MQTT to control the slideshow parameters remotely
see http://pi3d.github.io/html/FAQ.html and https://www.thedigitalpictureframe.com/control-your-digital-picture-frame-with-home-assistents-wifi-presence-detection-and-mqtt/
and https://www.cloudmqtt.com/plans.html

    ESC to quit, 's' to reverse, any other key to move on one.
'''
import os
import time
import random
import demo
import pi3d

from PIL import Image, ExifTags # these are needed for getting exif data from images

#####################################################
# these variables are constants
#####################################################
PIC_DIR = '/home/pi/Pictures' #'textures'
FPS = 20
FIT = True
EDGE_ALPHA = 0.0 # see background colour at edge. 1.0 would show reflection of image
BACKGROUND = (0, 0, 0, 1)
RESHUFFLE_NUM = 5 # times through before reshuffling
FONT_FILE = '/home/pi/pi3d_demos/fonts/NotoSans-Regular.ttf'
CODEPOINTS = '1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ., _-/' # limit to 49 ie 7x7 grid_size
USE_MQTT = False
CHKNUM = 30    # number of picture between re-loading file list
INTERACTIVE = False # set true to run without keyboard input.
SHOWTEXT = False # set tre to show text overlay.

#####################################################
# these variables can be altered using mqtt messaging
#####################################################
time_delay = 10.0 # between slides
fade_time = 3.0
shuffle = True # shuffle on reloading
date_from = None
date_to = None
quit = False
paused = False # NB must be set to True after the first iteration of the show!

delta_alpha = 1.0 / (FPS * fade_time) # delta alpha

#####################################################
# some functions to tidy subsequent code
#####################################################
def tex_load(fname, orientation):
  try:
    im = Image.open(fname)
    if orientation > 4: # this is a rotated image)
      im = im.transpose(Image.ROTATE_270)
    if orientation in [2, 4, 5, 8]:
      im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if orientation in [4, 8]:
      im = im.transpose(Image.FLIP_TOP_BOTTOM)

    tex = pi3d.Texture(fname, blend=True, m_repeat=True)
  except Exception as e:
    print('''Couldn't load file {}
            (probably related to Apple and or Adobe making assumptions about hardware!!): {}'''.format(fname, e))
    tex = None
  return tex

def tidy_name(path_name):
    name = os.path.basename(path_name).upper()
    name = ''.join([c for c in name if c in CODEPOINTS])
    return name

def get_files(dt_from=None, dt_to=None):
  # dt_from and dt_to are either None or tuples (2016,12,25)
  if dt_from is not None:
    dt_from = time.mktime(dt_from + (0, 0, 0, 0, 0, 0))
  if dt_to is not None:
    dt_to = time.mktime(dt_to + (0, 0, 0, 0, 0, 0))
  global shuffle, PIC_DIR, EXIF_DATID
  file_list = []
  extensions = ['.png','.jpg','.jpeg'] # can add to these
  for root, _dirnames, filenames in os.walk(PIC_DIR):
      for filename in filenames:
          ext = os.path.splitext(filename)[1].lower()
          if ext in extensions and not '.AppleDouble' in root and not filename.startswith('.'):
              file_path_name = os.path.join(root, filename)
              include_flag = True
              orientation = 1 # this is default - unrotated
              if EXIF_DATID is not None and EXIF_ORIENTATION is not None:
                try:
                  im = Image.open(file_path_name) # lazy operation so shouldn't load (better test though)
                  #print(filename, end="")
                  exif_data = im._getexif()
                  #print('orientation is {}'.format(exif_data[EXIF_ORIENTATION]))
                  dt = time.mktime(time.strptime(exif_data[EXIF_DATID], '%Y:%m:%d %H:%M:%S'))
                  orientation = int(exif_data[EXIF_ORIENTATION])
		  #1 = Horizontal (normal)
		  #2 = Mirror horizontal
		  #3 = Rotate 180
		  #4 = Mirror vertical
		  #5 = Mirror horizontal and rotate 270 CW
		  #6 = Rotate 90 CW
		  #7 = Mirror horizontal and rotate 90 CW
		  #8 = Rotate 270 CW
                except Exception as e: # NB should really check error here but it's almost certainly due to lack of exif data
                  print(e)
                  dt = os.path.getmtime(file_path_name) # so use file last modified date
                if (dt_from is not None and dt < dt_from) or (dt_to is not None and dt > dt_to):
                  include_flag = False
              if include_flag:
                file_list.append((file_path_name, orientation)) # iFiles now list of tuples (file_name, orientation) 
  if shuffle:
    random.shuffle(file_list) # randomize pictures
  else:
    file_list.sort() # if not suffled; sort by name
  return file_list, len(file_list) # tuple of file list, number of pictures

EXIF_DATID = None # this needs to be set before get_files() above can extract exif date info
EXIF_ORIENTATION = None
for k in ExifTags.TAGS:
  if ExifTags.TAGS[k] == 'DateTimeOriginal':
    EXIF_DATID = k
  if ExifTags.TAGS[k] == 'Orientation':
    EXIF_ORIENTATION = k

##############################################
# MQTT functionality - see https://www.thedigitalpictureframe.com/
##############################################
if USE_MQTT:
  try:
    import paho.mqtt.client as mqtt
    def on_connect(client, userdata, flags, rc):
      print("Connected to MQTT broker")

    def on_message(client, userdata, message):
      # TODO not ideal to have global but probably only reasonable way to do it
      global next_pic_num, iFiles, nFi, date_from, date_to, time_delay
      global delta_alpha, fade_time, shuffle, quit, paused, nexttm
      msg = message.payload.decode("utf-8")
      reselect = False
      if message.topic == "frame/date_from": # NB entered as mqtt string "2016:12:25"
        df = msg.split(":")
        date_from = tuple(int(i) for i in df)
        reselect = True
      elif message.topic == "frame/date_to":
        df = msg.split(":")
        date_to = tuple(int(i) for i in df)
        reselect = True
      elif message.topic == "frame/time_delay":
        time_delay = float(msg)
      elif message.topic == "frame/fade_time":
        fade_time = float(msg)
        delta_alpha = 1.0 / (FPS * fade_time)
      elif message.topic == "frame/shuffle":
        shuffle = True if msg == "True" else False
        reselect = True
      elif message.topic == "frame/quit":
        quit = True
      elif message.topic == "frame/paused":
        paused = not paused # toggle from previous value
      elif message.topic == "frame/back":
        next_pic_num -= 2
        if next_pic_num < -1:
          next_pic_num = -1
        nexttm = time.time() - 1.0
      if reselect:
        iFiles, nFi = get_files(date_from, date_to)
        next_pic_num = 0

    # set up MQTT listening
    client = mqtt.Client()
    client.username_pw_set("orhellow", "z6kfIctiONxP") # replace with your own id
    client.connect("postman.cloudmqtt.com", 16845, 60) # replace with your own server
    client.loop_start()
    client.subscribe("frame/date_from", qos=0)
    client.subscribe("frame/date_to", qos=0)
    client.subscribe("frame/time_delay", qos=0)
    client.subscribe("frame/fade_time", qos=0)
    client.subscribe("frame/shuffle", qos=0)
    client.subscribe("frame/quit", qos=0)
    client.subscribe("frame/paused", qos=0)
    client.subscribe("frame/back", qos=0)
    client.on_connect = on_connect
    client.on_message = on_message
  except Exception as e:
    print("MQTT not set up because of: {}".format(e))
##############################################

DISPLAY = pi3d.Display.create(frames_per_second=FPS, background=BACKGROUND)
CAMERA = pi3d.Camera(is_3d=False)

shader = pi3d.Shader("/home/pi/pi3d_demos/shaders/blend_new")
slide = pi3d.Sprite(camera=CAMERA, w=DISPLAY.width, h=DISPLAY.height, z=5.0)
slide.set_shader(shader)
slide.unif[47] = EDGE_ALPHA

#inserted BME
if INTERACTIVE:
  kbd = pi3d.Keyboard()

# images in iFiles list
nexttm = 0.0
iFiles, nFi = get_files(date_from, date_to)
next_pic_num = 0
sfg = None # slide for background
sbg = None # slide for foreground
if nFi == 0:
  print('No files selected!')
  exit()

# PointText and TextBlock
font = pi3d.Font(FONT_FILE, codepoints=CODEPOINTS, grid_size=7, shadow_radius=4.0,
                 shadow=(0,0,0,128))
text = pi3d.PointText(font, CAMERA, max_chars=200, point_size=50)
textblock = pi3d.TextBlock(x=-DISPLAY.width * 0.5 + 50, y=-DISPLAY.height * 0.4,
                           z=0.1, rot=0.0, char_count=199,
                           text_format="{}".format(tidy_name(iFiles[next_pic_num][0])), size=0.99, 
                           spacing="F", space=0.02, colour=(1.0, 1.0, 1.0, 1.0))
text.add_text_block(textblock)

num_run_through = 0
while DISPLAY.loop_running():
  tm = time.time()
  if nFi > 0:
    if tm > nexttm and not paused: # this must run first iteration of loop
      nexttm = tm + time_delay
      a = 0.0 # alpha - proportion front image to back
      sbg = sfg
      sfg = None
      while sfg is None: # keep going through until a usable picture is found TODO break out how?
        pic_num = next_pic_num

        #reload filelist after CHKNUM pictures shown
        if (pic_num % CHKNUM) == 0: # this will shuffle as well
          iFiles, nFi = get_files()
          pic_num = pic_num % nFi # just in case list is severly shortened

        sfg = tex_load(*iFiles[pic_num]) # '*' here splits tuple in two values
        next_pic_num += 1
        if next_pic_num >= nFi:
          num_run_through += 1
          if shuffle and num_run_through >= RESHUFFLE_NUM:
            num_run_through = 0
            random.shuffle(iFiles)
          next_pic_num = 0
      if sbg is None: # first time through
        sbg = sfg
      slide.set_textures([sfg, sbg])
      slide.unif[45:47] = slide.unif[42:44] # transfer front width and height factors to back
      slide.unif[51:53] = slide.unif[48:50] # transfer front width and height offsets
      wh_rat = (DISPLAY.width * sfg.iy) / (DISPLAY.height * sfg.ix)
      if (wh_rat > 1.0 and FIT) or (wh_rat <= 1.0 and not FIT):
        sz1, sz2, os1, os2 = 42, 43, 48, 49
      else:
        sz1, sz2, os1, os2 = 43, 42, 49, 48
        wh_rat = 1.0 / wh_rat
      slide.unif[sz1] = wh_rat
      slide.unif[sz2] = 1.0
      slide.unif[os1] = (wh_rat - 1.0) * 0.5
      slide.unif[os2] = 0.0
      # set the file name as the description
      if SHOWTEXT:
        textblock.set_text(text_format="{}".format(tidy_name(iFiles[pic_num][0])))
        text.regen()

    if a < 1.0:
      a += delta_alpha
      slide.unif[44] = a
      if SHOWTEXT:
        # this sets alpha for the TextBlock from 0 to 1 then back to 0
        textblock.colouring.set_colour(alpha=(1.0 - abs(1.0 - 2.0 * a)))
        text.regen()

    slide.draw()

  else:
    if SHOWTEXT:
      textblock.set_text("NO IMAGES SELECTED")
      textblock.colouring.set_colour(alpha=1.0)
      text.regen()
    else:
      print("NO IMAGES SELECTED")


  if SHOWTEXT:
    text.draw()

  if INTERACTIVE:
    k = kbd.read()
    if k != -1:
      nexttm = time.time() - 1.0
    if k==27 or quit: #ESC
      kbd.close()
      break
    if k==ord(' '):
      paused = not paused
    if k==ord('s'): # go back a picture
      next_pic_num -= 2
      if next_pic_num < -1:
        next_pic_num = -1

try:
  client.loop_stop()
except Exception as e:
  print("This was going to fail if previous try failed!")
DISPLAY.destroy()
