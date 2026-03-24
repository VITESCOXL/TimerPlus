
from olexFunctions import OlexFunctions
OV = OlexFunctions()

import os
import htmlTools
import olex
import olx
import gui
import olex_gui

import time
import json
import uuid

try:
  from RunPrg import RunRefinementPrg
except Exception:
  RunRefinementPrg = None
debug = bool(OV.GetParam("olex2.debug", False))


instance_path = OV.DataDir()

try:
  from_outside = False
  p_path = os.path.dirname(os.path.abspath(__file__))
except:
  from_outside = True
  p_path = os.path.dirname(os.path.abspath("__file__"))

l = open(os.sep.join([p_path, 'def.txt'])).readlines()
d = {}
for line in l:
  line = line.strip()
  if not line or line.startswith("#"):
    continue
  d[line.split("=")[0].strip()] = line.split("=")[1].strip()

p_name = d['p_name']
p_htm = d['p_htm']
p_img = eval(d['p_img'])
p_scope = d['p_scope']

OV.SetVar('TimerPlus_plugin_path', p_path)

from PluginTools import PluginTools as PT

class TimerPlus(PT):

  def __init__(self):
    super(TimerPlus, self).__init__()
    self.p_name = p_name
    self.p_path = p_path
    self.p_scope = p_scope
    self.p_htm = p_htm
    self.p_img = p_img
    self.deal_with_phil(operation='read')
    self.print_version_date()
    
    # Initialize per-molecule timing system
    self.timing_data_file = os.path.join(instance_path, 'TimerPlus_history.json')
    self.molecule_timings = self.load_timing_data()
    self.current_molecule = None
    self.current_start_time = None
    self.current_idle_start = None
    self._last_auto_save = time.time()
    self._save_interval = 10  # Auto-save every 10 seconds
    self._session_refine_time = 0.0  # refine time accumulated since last save
    self._orig_refine_run = None
    self.sNumPath = None
    self.sNum = None
    
    OV.registerFunction(self.print_formula,True,self.p_name)
    OV.registerFunction(self.get_idle_time,True,self.p_name)
    OV.registerFunction(self.get_work_time,True,self.p_name)
    OV.registerFunction(self.get_running_time,True,self.p_name)
    OV.registerFunction(self.get_molecule_name,True,self.p_name)
    OV.registerFunction(self.get_timing_history,True,self.p_name)
    OV.registerFunction(self.update_timing,True,self.p_name)
    OV.registerFunction(self.reset_current_timing,True,self.p_name)
    OV.registerFunction(self.refresh_display,True,self.p_name)
    OV.registerFunction(self.get_session_time,True,self.p_name)
    OV.registerFunction(self.get_refine_time,True,self.p_name)
    OV.registerFunction(self.get_work_time_for_dataset,True,self.p_name)
    if not from_outside:
      self.setup_gui()
    # END Generated =======================================

    # Auto-start: begin session timer immediately on GUI launch
    self.session_start_time = time.time()

    # Auto-start: register callback so timer starts whenever a structure is opened
    self._register_file_listener()

    # Refine timing: wrap RunRefinementPrg.run to capture time directly
    self._register_refine_timing()

    # Auto-start: initialise timing for any structure already loaded at startup
    self.check_and_switch_molecule()

    # Patch multiple datasets so work-time badges appear in multi-CIF dataset buttons
    self._patch_multiple_dataset()

  def _patch_multiple_dataset(self):
    try:
      import gui.home as home_module
      mds = home_module.mds
      timer_instance = self
      if not getattr(mds, '_timerplus_patched', False):
        orig_list_datasets = mds.list_datasets

        def patched_list_datasets(sort_key):
          rv = orig_list_datasets(sort_key)
          result = []
          for entry in rv:
            index, name, display, sk, do_show = entry
            if do_show and name:
              work_time = timer_instance.get_work_time_for_dataset(name)
              if work_time:
                display = '%s [%s]' % (display, work_time)
            result.append((index, name, display, sk, do_show))
          return result

        mds.list_datasets = patched_list_datasets
        mds._timerplus_patched = True
        if debug:
          print('TimerPlus: patched mds.list_datasets')
    except Exception as e:
      print('TimerPlus: could not patch mds: %s' % str(e))

  def load_timing_data(self):
    """Load timing history from JSON file"""
    try:
      if os.path.exists(self.timing_data_file):
        with open(self.timing_data_file, 'r') as f:
          return json.load(f)
      else:
        return {}
    except:
      return {}
  
  def save_timing_data(self):
    """Save timing history to JSON file"""
    try:
      with open(self.timing_data_file, 'w') as f:
        json.dump(self.molecule_timings, f, indent=2)
    except Exception as e:
      print("Error saving timing data: %s" % str(e))

    """Save every tracked molecule to its own local _timer.json in its sNumPath directory."""
    for mol_name, mol_data in self.molecule_timings.items():
      try:
        strdir = mol_data.get('sNumPath') or OV.StrDir()
        if not strdir:
          strdir = instance_path
        fn = os.path.join(strdir, '%s_timer.json' % mol_name)
        with open(fn, 'w') as f:
          json.dump(mol_data, f, indent=2)
        if debug:
          print("TimerPlus: saved %s_timer.json to %s" % (mol_name, strdir))
      except Exception as e:
        print("Error saving local timing data for %s: %s" % (mol_name, str(e)))



  # ------------------------------------------------------------------
  # Auto-start helpers
  # ------------------------------------------------------------------

  def _register_file_listener(self):
    """Register onto olx.FileChangeListeners so the timer auto-starts
    whenever a structure is opened in Olex2."""
    try:
      if not hasattr(olx, 'FileChangeListeners'):
        olx.FileChangeListeners = []
      if self._on_file_changed not in olx.FileChangeListeners:
        olx.FileChangeListeners.append(self._on_file_changed)
      if debug:
        print("TimerPlus: file-change listener registered")
    except Exception as e:
      print("TimerPlus: could not register file-change listener: %s" % str(e))

  def _on_file_changed(self, filetype):
    """Called automatically by Olex2 whenever a structure is opened."""
    try:
      self.check_and_switch_molecule()
      if self.current_molecule and self.current_molecule != "No structure loaded":
        print("TimerPlus: auto-started timing for '%s'" % self.current_molecule)
      try:
        olx.html.Update()
      except:
        pass
    except Exception as e:
      if debug:
        print("TimerPlus _on_file_changed error: %s" % str(e))

  def _register_refine_timing(self):
    """Wrap RunRefinementPrg.run so refinement time is captured synchronously.
    Refinement blocks the main thread, so polling cannot work — only a wrap does."""
    if RunRefinementPrg is None:
      return
    try:
      timer = self
      _orig = RunRefinementPrg.run
      self._orig_refine_run = _orig

      def _timed_run(rp_self):
        start = time.time()
        try:
          return _orig(rp_self)
        finally:
          elapsed = max(0.0, time.time() - start)
          timer._session_refine_time += elapsed
          if debug:
            print("TimerPlus: refinement took %.1fs" % elapsed)

      RunRefinementPrg.run = _timed_run
      if debug:
        print("TimerPlus: RunRefinementPrg.run wrapped")
    except Exception as e:
      print("TimerPlus: could not wrap RunRefinementPrg.run: %s" % str(e))

  def _unregister_refine_timing(self):
    """Restore original RunRefinementPrg.run."""
    if RunRefinementPrg is not None and self._orig_refine_run is not None:
      try:
        RunRefinementPrg.run = self._orig_refine_run
        self._orig_refine_run = None
      except Exception as e:
        if debug:
          print("TimerPlus: could not restore RunRefinementPrg.run: %s" % str(e))

  def __del__(self):
    """Restore RunRefinementPrg.run and save timing on unload."""
    try:
      self.save_current_molecule_timing()
    except:
      pass
    self._unregister_refine_timing()

  def get_session_time(self):
    """Return the total seconds since Olex2 (the plugin) was launched."""
    try:
      return round(float(time.time() - self.session_start_time), 1)
    except:
      return 0.0

  def get_refine_time(self):
    """Get accumulated refinement time for current molecule."""
    self.check_and_switch_molecule()
    try:
      mol = self.current_molecule
      if not mol or mol == "No structure loaded":
        return 0.0
      saved = self.molecule_timings.get(mol, {}).get('total_refine_time', 0.0)
      return round(float(saved + self._session_refine_time), 1)
    except:
      return 0.0

  # ------------------------------------------------------------------

  def check_and_switch_molecule(self):
    """Check if molecule has changed and switch timing context"""
    mol_name = self._get_molecule_name_internal()
    
    # Periodic auto-save (every 10 seconds)
    if time.time() - self._last_auto_save > self._save_interval:
      if self.current_molecule and self.current_molecule != "No structure loaded":
        self.save_current_molecule_timing()
      self._last_auto_save = time.time()
    
    if mol_name != self.current_molecule:
      # Save current molecule timing if exists
      if self.current_molecule and self.current_molecule != "No structure loaded":
        self.save_current_molecule_timing()
      
      # Switch to new molecule
      self.current_molecule = mol_name
      if mol_name != "No structure loaded":
        if mol_name not in self.molecule_timings:
          self.molecule_timings[mol_name] = {
            'total_work_time': 0.0,
            'total_idle_time': 0.0,
            'total_refine_time': 0.0,
            'total_run_time': 0.0,
            'filepath': "",
            'sNum': OV.ModelSrc(),
            'uuid': str(uuid.uuid4()),
            'last_updated': time.strftime('%Y-%m-%d %H:%M:%S')
          }
          self.save_timing_data()
        self.current_start_time = time.time()
        self._session_refine_time = 0.0
        olex_gui.ResetIdleTime()
    else:
      # Same molecule, ensure it exists in timings
      if mol_name != "No structure loaded" and mol_name not in self.molecule_timings:
        self.molecule_timings[mol_name] = {
          'total_work_time': 0.0,
          'total_idle_time': 0.0,
          'total_refine_time': 0.0,
          'total_run_time': 0.0,
          'last_updated': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        self.save_timing_data()
        if self.current_start_time is None:
          self.current_start_time = time.time()
          olex_gui.ResetIdleTime()
  
  def save_current_molecule_timing(self):
    """Save timing for current molecule"""
    if not self.current_molecule or self.current_molecule == "No structure loaded":
      return
    
    if self.current_start_time is not None:
      elapsed = time.time() - self.current_start_time
      idle = olex_gui.GetIdleTime()
      work = max(0, elapsed - idle - self._session_refine_time)
      
      if self.current_molecule in self.molecule_timings:
        self.molecule_timings[self.current_molecule]['total_work_time'] += work
        self.molecule_timings[self.current_molecule]['total_idle_time'] += idle
        self.molecule_timings[self.current_molecule]['total_refine_time'] = (
          self.molecule_timings[self.current_molecule].get('total_refine_time', 0.0) + self._session_refine_time)
        self.molecule_timings[self.current_molecule]['total_run_time'] += elapsed
        self.molecule_timings[self.current_molecule]['last_updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
        self.molecule_timings[self.current_molecule].setdefault('sNumPath', self.sNumPath)
        self.molecule_timings[self.current_molecule].setdefault('sNum', self.sNum)
        self.molecule_timings[self.current_molecule].setdefault('uuid', str(uuid.uuid4()))
      self.save_timing_data()
      
      # Reset timers to avoid double-counting
      self.current_start_time = time.time()
      olex_gui.ResetIdleTime()
      self._session_refine_time = 0.0
  
  def _get_molecule_name_internal(self):
    """Internal method to get molecule name"""
    try:
      sNum, sNumPath = get_sNum_and_path()
      if sNum:
        self.sNum = sNum
        self.sNumPath = sNumPath
        name = sNum
        return name if name else "No structure loaded"
      else:
        return "No structure loaded"
    except:
      return "No structure loaded"

  def print_formula(self):
    self.check_and_switch_molecule()
    formula = {}
    for element in str(olx.xf.GetFormula('list')).split(','):
      element_type, n = element.split(':')
      print("%s: %s" %(element_type, n))
      formula.setdefault(element_type, float(n))
      
    print("Molecule: %s" % self.current_molecule)
    print("Idle time: %.1f" %(self.get_idle_time()))
    print("Work time: %.1f" %(self.get_work_time()))
    print("Running time: %.1f"  %(self.get_running_time()))
    try:
      olx.html.Update()
    except:
      try:
        olex.m("html.Update()")
      except:
        pass

  def get_idle_time(self):
    """Get idle time for current molecule"""
    self.check_and_switch_molecule()
    try:
      if self.current_molecule == "No structure loaded" or self.current_molecule is None:
        return 0.0
      current_idle = olex_gui.GetIdleTime()
      total_idle = self.molecule_timings.get(self.current_molecule, {}).get('total_idle_time', 0.0)
      return round(float(total_idle + current_idle), 1)
    except Exception as e:
      if debug:
        print("Error in get_idle_time: %s" % str(e))
      return 0.0
  
  def get_work_time(self):
    """Get work time for current molecule"""
    self.check_and_switch_molecule()
    try:
      if self.current_molecule == "No structure loaded" or self.current_molecule is None:
        return 0.0
      if self.current_start_time is None:
        # Timer not started yet, initialize it
        self.current_start_time = time.time()
        olex_gui.ResetIdleTime()
        if debug:
          print("Timer started for: %s" % self.current_molecule)
        return 0.0
      elapsed = time.time() - self.current_start_time
      idle = olex_gui.GetIdleTime()
      session_refine = self._session_refine_time
      work = max(0, elapsed - idle - session_refine)
      total_work = self.molecule_timings.get(self.current_molecule, {}).get('total_work_time', 0.0)
      result = round(float(total_work + work), 1)
      if debug:
        print("Work time for %s: %.1f (session: %.1f + saved: %.1f)" % (self.current_molecule, result, work, total_work))
      return result
    except Exception as e:
      if debug:
        print("Error in get_work_time: %s" % str(e))
      return 0.0
  
  def get_running_time(self):
    """Get running time for current molecule"""
    self.check_and_switch_molecule()
    try:
      if self.current_molecule == "No structure loaded" or self.current_molecule is None:
        return 0.0
      if self.current_start_time is None:
        # Timer not started yet, initialize it
        self.current_start_time = time.time()
        olex_gui.ResetIdleTime()
        return 0.0
      elapsed = time.time() - self.current_start_time
      total_run = self.molecule_timings.get(self.current_molecule, {}).get('total_run_time', 0.0)
      return round(float(total_run + elapsed), 1)
    except Exception as e:
      if debug:
        print("Error in get_running_time: %s" % str(e))
      return 0.0
  
  def get_molecule_name(self):
    """Get the current structure/molecule name"""
    self.check_and_switch_molecule()
    return self.current_molecule if self.current_molecule else "No structure loaded"
  
  def update_timing(self):
    """Force update and save current molecule timing"""
    self.check_and_switch_molecule()
    self.save_current_molecule_timing()
    try:
      olx.html.Update()
    except:
      pass
    return "Timing saved and updated"
  
  def refresh_display(self):
    """Refresh the display to show current timing"""
    self.check_and_switch_molecule()
    try:
      olx.html.Update()
    except:
      pass
    return "Display refreshed"
  
  def reset_current_timing(self):
    """Reset timing for current molecule"""
    mol_name = self._get_molecule_name_internal()
    if mol_name and mol_name != "No structure loaded":
      if mol_name in self.molecule_timings:
        del self.molecule_timings[mol_name]
        self.save_timing_data()
      self.current_start_time = time.time()
      self.current_idle_start = 0
      olex_gui.ResetIdleTime()
      try:
        olx.html.Update()
      except:
        pass
      return "Timing reset for %s" % mol_name
    return "No structure loaded"
  
  def get_timing_history(self):
    """Get formatted HTML table of timing history for all molecules"""
    self.check_and_switch_molecule()
    
    # Get current session times
    current_work = 0.0
    current_idle = 0.0
    current_total = 0.0
    
    if self.current_molecule and self.current_molecule != "No structure loaded" and self.current_start_time is not None:
      elapsed = time.time() - self.current_start_time
      idle = olex_gui.GetIdleTime()
      current_work = max(0, elapsed - idle)
      current_idle = idle
      current_total = elapsed
    
    # Collect all molecules to display (including current even if not in history)
    molecules_to_show = {}
    
    # Add all saved molecules
    for mol_name, data in self.molecule_timings.items():
      molecules_to_show[mol_name] = {
        'work': data.get('total_work_time', 0.0),
        'refine': data.get('total_refine_time', 0.0),
        'idle': data.get('total_idle_time', 0.0),
        'total': data.get('total_run_time', 0.0),
        'updated': data.get('last_updated', 'Unknown'),
        'is_current': False
      }
    
    # Current session refine for display
    current_session_refine = self._session_refine_time

    # Add or update current molecule
    if self.current_molecule and self.current_molecule != "No structure loaded":
      if self.current_molecule in molecules_to_show:
        molecules_to_show[self.current_molecule]['work'] += current_work
        molecules_to_show[self.current_molecule]['refine'] += current_session_refine
        molecules_to_show[self.current_molecule]['idle'] += current_idle
        molecules_to_show[self.current_molecule]['total'] += current_total
        molecules_to_show[self.current_molecule]['updated'] = "Active Now"
        molecules_to_show[self.current_molecule]['is_current'] = True
      else:
        # Current molecule not in history yet, show it anyway
        molecules_to_show[self.current_molecule] = {
          'work': current_work,
          'refine': current_session_refine,
          'idle': current_idle,
          'total': current_total,
          'updated': "Active Now",
          'is_current': True
        }
    
    if not molecules_to_show:
      return "<tr><td style='text-align:center;'>No timing data available.<br/>Load a structure to start tracking.</td></tr>"
    
    html_rows = []
    # Sort by current first, then by last updated
    sorted_molecules = sorted(
      molecules_to_show.items(),
      key=lambda x: (not x[1]['is_current'], x[1]['updated'] if x[1]['updated'] != "Active Now" else "9999"),
      reverse=True
    )
    
    for mol_name, data in sorted_molecules:
      work_str = self._format_time(data['work'])
      refine_str = self._format_time(data['refine'])
      idle_str = self._format_time(data['idle'])
      total_str = self._format_time(data['total'])
      
      # Highlight current molecule
      bg_color = "#e8f4f8" if data['is_current'] else "#ffffff"
      
      html_rows.append(
        "<tr style='background-color: %s;'>" % bg_color +
        "<td width='25%%' style='padding:6px;'><b>%s</b></td>" % mol_name +
        "<td width='15%%' style='padding:6px; text-align:center;'>%s</td>" % work_str +
        "<td width='15%%' style='padding:6px; text-align:center;'>%s</td>" % refine_str +
        "<td width='15%%' style='padding:6px; text-align:center;'>%s</td>" % idle_str +
        "<td width='15%%' style='padding:6px; text-align:center;'>%s</td>" % total_str +
        "<td width='15%%' style='padding:6px; text-align:center;'>%s</td>" % data['updated'] +
        "</tr>"
      )
    
    return "\n".join(html_rows)
  
  def _format_time(self, seconds):
    """Format seconds as HH:MM:SS"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return "%02d:%02d:%02d" % (hours, minutes, secs)

  def get_work_time_for_dataset(self, dataset_name):
    """Return formatted HH:MM:SS work time for a named dataset from its _timer.json, or '' if unavailable."""
    try:
      strdir = olx.FilePath()
      if not strdir:
        return ''
      fn = os.path.join(strdir, '%s_timer.json' % dataset_name)
      if not os.path.exists(fn):
        return ''
      with open(fn, 'r') as f:
        data = json.load(f)
      secs = float(data.get('total_work_time', 0.0))
      return self._format_time(secs)
    except Exception:
      return ''
  

  def get_or_create_structure(directory, name):
    json_path = os.path.join(directory, f"{name}_worklog.json")
    
    if os.path.exists(json_path):
      with open(json_path) as f:
        data = json.load(f)
      uuid = data["structure_uuid"]
    else:
      uuid = str(uuid4())
      # JSON will be written when the first session is recorded
    
    conn = DBConnection().conn
    row = conn.execute(
      "SELECT id, directory FROM structure WHERE uuid = ?", (uuid,)
    ).fetchone()
    
    if row:
      struct_id, known_dir = row
      if os.path.normpath(known_dir) != os.path.normpath(directory):
        logger.info("Structure %r moved from %r to %r.", name, known_dir, directory)
        conn.execute(
          "UPDATE structure SET directory = ?, name = ? WHERE id = ?",
          (directory, name, struct_id)
        )
        conn.commit()
      return struct_id
    
    # First time this structure has been seen by the DB
    cursor = conn.execute(
      "INSERT INTO structure (uuid, directory, name) VALUES (?, ?, ?)",
      (uuid, directory, name)
    )
    conn.commit()
    return cursor.lastrowid  
  

TimerPlus_instance = TimerPlus()
print("TimerPlus loaded OK.")
mol = TimerPlus_instance.current_molecule
if mol and mol != "No structure loaded":
  print("TimerPlus: timing started for '%s'" % mol)
else:
  print("TimerPlus: session timer running - timing will auto-start when a structure is opened.")
  
def get_sNum_and_path():
  """Return a stable, globally unique identifier for the current structure."""
  if olx.IsFileType("ires") == 'true':
    sNum = OV.ModelSrc()
  else:
    sNum = olx.xf.DataName(int(olx.xf.CurrentData()))
  # Combine with the directory to make it globally unique
  directory = os.path.normpath(OV.FilePath())
  return sNum, directory  
  
