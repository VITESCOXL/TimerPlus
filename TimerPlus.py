from olexFunctions import OlexFunctions
OV = OlexFunctions()

import os
import olex
import olx
import olex_gui

import time
import json
import uuid
import re
from datetime import datetime
try:
  from RunPrg import RunRefinementPrg, LM
except Exception:
  RunRefinementPrg = None
  LM = None


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
    self._registered_refine_listeners = False
    self.sNumPath = None
    self.sNum = None
    self._refresh_interval = None
    self._refresh_active = False
    self._idle_seconds = 0.0
    self._idle_last_update = time.time()
    self._last_activity_time = time.time()
    self._idle_grace = float(OV.GetParam('TimerPlus.idle_grace', 2.0) or 2.0)
    self._last_mouse_pos = None
    
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
    OV.registerFunction(self.update_timer_vars,True,self.p_name)
    OV.registerFunction(self._tick,True,self.p_name)
    OV.registerFunction(self._retry_nospher,True,self.p_name)
    OV.registerFunction(self.getPublicationContact, True, self.p_name)
    OV.registerFunction(self.show_history, True, self.p_name)
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

    # Initialise display variables so the HTML panel never shows missing-var errors
    for _var in ('TIMER_MOL', 'TIMER_WORK', 'TIMER_REFINE', 'TIMER_IDLE', 'TIMER_RUN', 'TIMER_USER'):
      OV.SetVar(_var, '')
    self.update_timer_vars()

    # Patch multiple datasets so work-time badges appear in multi-CIF dataset buttons
    self._patch_multiple_dataset()

    # Start recurring display refresh based on phil refresh_interval
    self._start_refresh_timer()

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

  def _get_user_info(self):
    """Resolve publication contact using: GUI control → params → DB.

    Returns dict: {'id', 'displayname', 'email', 'affiliationid'}.
    Prefer the first non-empty source. No OS fallback.
    """
    info = {'id': None, 'displayname': None, 'email': None, 'affiliationid': None}

    # Helper to attempt resolving a literal value via persons DB
    def _resolve_via_db(val, persons):
      if not val:
        return None
      # Try person lookup by arbitrary value
      try:
        pid = None
        try:
          pid = persons.findPersonId(val)
        except Exception:
          pass
        if not pid:
          try:
            pid = int(val)
          except Exception:
            pid = None
        if pid:
          try:
            p = persons.get_person(pid)
            if p:
              return {
                'id': getattr(p, 'id', None),
                'displayname': p.get_display_name() if hasattr(p, 'get_display_name') else None,
                'email': getattr(p, 'email', None),
                'affiliationid': getattr(p, 'affiliationid', None)
              }
          except Exception:
            return None
      except Exception:
        return None
      return None

    # Try GUI control first (live value shown in Report->Publications)
    try:
      ctrl_val = self.read_publication_contact_control()
      if ctrl_val:
        # Try DB resolution if available
        try:
          import userDictionaries
          if getattr(userDictionaries, 'persons', None) is None:
            try:
              userDictionaries.init_userDictionaries()
            except Exception:
              try:
                userDictionaries.DBConnection()
              except Exception:
                pass
          persons = userDictionaries.persons
        except Exception:
          persons = None

        if persons:
          resolved = _resolve_via_db(ctrl_val, persons)
          if resolved:
            return resolved
        # Fallback: use literal control string
        info['displayname'] = str(ctrl_val)
        return info
    except Exception:
      pass

    # Next: try params (operator/submitter/user)
    try:
      try_names = [
        OV.GetParam('snum.report.operator', None),
        OV.GetParam('snum.report.submitter', None),
        OV.GetParam('user.report.user', None),
      ]
    except Exception:
      try_names = [None, None, None]

    # Attempt to access DB-backed persons API once
    persons = None
    try:
      import userDictionaries
      if getattr(userDictionaries, 'persons', None) is None:
        try:
          userDictionaries.init_userDictionaries()
        except Exception:
          try:
            userDictionaries.DBConnection()
          except Exception:
            pass
      persons = userDictionaries.persons
    except Exception:
      persons = None

    for name in try_names:
      if not name:
        continue
      # Prefer DB resolution when possible
      if persons:
        resolved = _resolve_via_db(name, persons)
        if resolved:
          return resolved
      # If not resolved via DB, treat as literal display name
      info['displayname'] = str(name)
      return info

    return info

  def _get_current_user_display(self):
    """Return a short display name for the current GUI publication contact, or empty string."""
    try:
      ui = self._get_user_info()
      if ui and ui.get('displayname'):
        return str(ui.get('displayname'))
      # Fallback to GUI control raw strings
      try:
        ctrl = self.read_publication_contact_control()
        if ctrl and str(ctrl).strip() and str(ctrl) not in ('', '?'):
          return str(ctrl)
      except Exception:
        pass
    except Exception:
      pass
    return ''

  def _sanitize_for_filename(self, name):
    """Return a filesystem-safe version of name for use in filenames."""
    try:
      s = str(name)
      # Replace path separators and problematic chars
      for ch in ('/', '\\', ':', '*', '?', '"', '<', '>', '|'):
        s = s.replace(ch, '_')
      s = re.sub(r'\s+', '_', s).strip('_')
      if not s:
        s = 'unnamed'
      return s
    except Exception:
      return 'unnamed'
  
  def save_timing_data(self):
    """Save timing history to JSON file"""
    # Write atomically to avoid truncating the existing history file
    try:
      tmp_fn = self.timing_data_file + '.tmp'
      with open(tmp_fn, 'w', encoding='utf-8') as f:
        json.dump(self.molecule_timings, f, indent=2, ensure_ascii=False, default=str)
      try:
        os.replace(tmp_fn, self.timing_data_file)
      except Exception:
        # Fallback to rename if replace not available
        try:
          os.remove(self.timing_data_file)
        except Exception:
          pass
        os.rename(tmp_fn, self.timing_data_file)
    except Exception as e:
      print("Error saving timing data: %s" % str(e))
      # Clean up temp file if present
      try:
        if os.path.exists(tmp_fn):
          os.remove(tmp_fn)
      except Exception:
        pass

    """Save every tracked molecule to its own local _timer.json in its sNumPath directory."""
    for mol_name, mol_data in list(self.molecule_timings.items()):
      try:
        strdir = mol_data.get('sNumPath') or OV.StrDir()
        if not strdir:
          strdir = instance_path
        safe_name = self._sanitize_for_filename(mol_name)
        fn = os.path.join(strdir, '%s_timer.json' % safe_name)
        # Ensure per-molecule JSON contains a resolved user entry when available
        try:
          if not mol_data.get('user') or not mol_data.get('user', {}).get('displayname'):
            # Prefer to populate from current molecule context
            if mol_name == self.current_molecule:
              try:
                ui = self._get_user_info()
                if ui and (ui.get('displayname') or ui.get('id')):
                  mol_data['user'] = ui
              except Exception:
                pass
        except Exception:
          pass
        # Write per-molecule file atomically to avoid partial/truncated files
        try:
          tmp_fn = fn + '.tmp'
          with open(tmp_fn, 'w', encoding='utf-8') as f:
            json.dump(mol_data, f, indent=2, ensure_ascii=False, default=str)
          try:
            os.replace(tmp_fn, fn)
          except Exception:
            try:
              os.remove(fn)
            except Exception:
              pass
            os.rename(tmp_fn, fn)
        except Exception as e:
          print("Error saving local timing data for %s: %s" % (mol_name, str(e)))
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
    except Exception as e:
      print("TimerPlus: could not register file-change listener: %s" % str(e))

  def _on_file_changed(self, filetype):
    """Called automatically by Olex2 whenever a structure is opened."""
    try:
      self._mark_activity()
      self.check_and_switch_molecule()
      if self.current_molecule and self.current_molecule != "No structure loaded":
        print("TimerPlus: auto-started timing for '%s'" % self.current_molecule)
      try:
        olx.html.Update()
      except:
        pass
    except Exception as e:
      pass

  def _register_refine_timing(self):
    """Wrap RunRefinementPrg.run so refinement time is captured synchronously.
    Refinement blocks the main thread, so polling cannot work — only a wrap does."""
    # Prefer using RunPrg.ListenerManager (LM) start/end callbacks if available
    if RunRefinementPrg is None or LM is None:
      return
    try:
      LM.register_listener(self._on_refine_start, "onStart")
      LM.register_listener(self._on_refine_end, "onEnd")
      self._registered_refine_listeners = True
    except Exception as e:
      print("TimerPlus: could not register refine listeners: %s" % str(e))

  def _on_refine_start(self, caller):
    """Listener called by RunPrg when a run/refine starts."""
    try:
      self._mark_activity()
      self._refine_start_time = time.time()
      self._refine_active = True
      # Keep idle clock timestamp current so no gap is counted on resume
      self._idle_last_update = time.time()
    except Exception:
      pass

  def _on_refine_end(self, caller):
    """Listener called by RunPrg when a run/refine ends."""
    try:
      start = getattr(self, '_refine_start_time', None)
      if start is not None:
        elapsed = max(0.0, time.time() - start)
      else:
        elapsed = 0.0
      self._session_refine_time += elapsed
      self._refine_active = False
      # Advance the idle clock baseline so the refine period is not counted as idle
      self._idle_last_update = time.time()
      self._mark_activity()
      # After a refine ends, attempt to parse NoSpher output.
      # Schedule a retry 4 s later so the file has time to be fully written.
      try:
        print("TimerPlus: _on_refine_end -> scheduling _scan_and_apply_nospher in 4s")
        olx.Schedule(4, "spy.TimerPlus._retry_nospher()")
      except Exception as e:
        print("TimerPlus: schedule failed, trying immediately:", e)
        try:
          self._scan_and_apply_nospher()
        except Exception as e2:
          print("TimerPlus: _scan_and_apply_nospher failed:", e2)
    except Exception:
      pass

  def _unregister_refine_timing(self):
    """Unregister listeners registered with RunPrg.LM."""
    try:
      if LM is not None and self._registered_refine_listeners:
        try:
          LM.unregister_listener(self._on_refine_start, "onStart")
        except Exception:
          pass
        try:
          LM.unregister_listener(self._on_refine_end, "onEnd")
        except Exception:
          pass
        self._registered_refine_listeners = False
    except Exception:
      pass

  def _start_refresh_timer(self):
    """Schedule recurring display refresh using olx.Schedule"""
    try:
      interval = int(OV.GetParam('TimerPlus.refresh_interval', 3))
    except Exception:
      interval = 3
    if interval <= 0:
      self._stop_refresh_timer()
      return
    self._refresh_interval = interval
    self._refresh_active = True
    olx.Schedule(interval, "spy.TimerPlus._tick()")
    print("TimerPlus: olx.Schedule refresh started (interval=%s)" % str(interval))

  def _tick(self):
    """Called by olx.Schedule"""
    if not self._refresh_active:
      return
    try:
      # Idle is now plugin-side, so scheduled control updates are safe again.
      self._sample_pointer_activity()
      self.check_and_switch_molecule()
      self.update_timer_vars(push_controls=True)
      try:
        olx.html.Update()
      except Exception:
        pass
    except Exception:
      pass
    if self._refresh_active and self._refresh_interval and self._refresh_interval > 0:
      olx.Schedule(self._refresh_interval, "spy.TimerPlus._tick()")

  def _stop_refresh_timer(self):
    self._refresh_active = False

  def _reset_idle_tracking(self, reset_gui=False):
    """Reset plugin-side idle accumulation and optionally reset Olex idle counter."""
    now = time.time()
    self._idle_seconds = 0.0
    self._idle_last_update = now
    self._last_activity_time = now
    self._last_mouse_pos = None
    if reset_gui:
      try:
        olex_gui.ResetIdleTime()
      except Exception:
        pass
 

  def _mark_activity(self):
    self._last_activity_time = time.time()

  def _sample_pointer_activity(self):
    """Treat mouse motion as user activity for plugin-side idle tracking."""
    try:
      x = int(olx.GetMouseX())
      y = int(olx.GetMouseY())

      # Only count activity while pointer is inside the Olex2 GL viewport.
      ws = [int(v) for v in olx.GetWindowSize('gl').split(',')]
      if len(ws) >= 4:
        x0, y0, w, h = ws[0], ws[1], ws[2], ws[3]
        inside_local = (0 <= x < w) and (0 <= y < h)
        inside_absolute = (x0 <= x < (x0 + w)) and (y0 <= y < (y0 + h))
        if not (inside_local or inside_absolute):
          self._last_mouse_pos = None
          return

      pos = (x, y)
      if self._last_mouse_pos is None:
        self._last_mouse_pos = pos
        return
      if pos != self._last_mouse_pos:
        self._mark_activity()
      self._last_mouse_pos = pos
    except Exception:
      pass

  def _update_idle_clock(self):
    """Advance plugin-side idle counter using recent activity timestamps."""
    now = time.time()
    dt = max(0.0, now - self._idle_last_update)
    self._idle_last_update = now
    # Do not count idle while refinement is running
    if not getattr(self, '_refine_active', False) and (now - self._last_activity_time) >= self._idle_grace:
      self._idle_seconds += dt
    return self._idle_seconds

  def _get_idle_seconds(self):
    """Return idle seconds derived from plugin-tracked activity."""
    self._sample_pointer_activity()
    return self._update_idle_clock()

  

  def __del__(self):
    """Restore RunRefinementPrg.run and save timing on unload."""
    self._stop_refresh_timer()
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
    self.check_and_switch_molecule(do_autosave=False)
    try:
      mol = self.current_molecule
      if not mol or mol == "No structure loaded":
        return 0.0
      saved = self.molecule_timings.get(mol, {}).get('total_refine_time', 0.0)
      return round(float(saved + self._session_refine_time), 1)
    except:
      return 0.0

  # publication/contact helpers removed as requested

  def getCurrentUserName(self):
    try:
      return ''
    except Exception:
      return ''

  def getPublicationContact(self, param_name):
    """Return a display name for the given publication param for GUI inputs.

    Called from GUI as `spy.TimerPlus.getPublicationContact('snum.report.submitter')`.
    Preference: param value -> DB lookup -> literal param -> empty string.
    """
    try:
      if not param_name:
        return ''
      try:
        val = OV.GetParam(param_name, None)
      except Exception:
        val = None
      if not val:
        return ''

      # Try to resolve via userDictionaries persons DB
      try:
        import userDictionaries
        if getattr(userDictionaries, 'persons', None) is None:
          try:
            userDictionaries.init_userDictionaries()
          except Exception:
            try:
              userDictionaries.DBConnection()
            except Exception:
              pass
        persons = userDictionaries.persons
      except Exception:
        persons = None

      if persons:
        try:
          pid = None
          try:
            pid = persons.findPersonId(val)
          except Exception:
            pass
          if not pid:
            try:
              pid = int(val)
            except Exception:
              pid = None
          if pid:
            p = persons.get_person(pid)
            if p:
              return p.get_display_name() if hasattr(p, 'get_display_name') else str(val)
        except Exception:
          pass

      # Fallback: return literal param string
      return str(val)
    except Exception:
      return ''

  def read_publication_contact_control(self):
    """Read Contact Author from Report->Publications GUI control."""
    try:
      # Prefer the CIF-backed GUI value (used in templates): _publ_contact_author_name
      try:
        cif_name = OV.get_cif_item('_publ_contact_author_name', None)
      except Exception:
        cif_name = None
      if cif_name and str(cif_name).strip() and str(cif_name) not in ('', '?', "''"):
        return str(cif_name)

      # Fallback to the named GUI controls if present
      try:
        val = OV.GetControlValue('SET_SNUM_METACIF_OPERATOR')
      except Exception:
        val = None
      if val and val not in ('', '?'):
        return str(val)
      try:
        val2 = OV.GetControlValue('SET_SNUM_METACIF_SUBMITTER')
      except Exception:
        val2 = None
      if val2 and val2 not in ('', '?'):
        return str(val2)
    except Exception:
      pass
    return ''

  # ------------------------------------------------------------------

  def check_and_switch_molecule(self, do_autosave=True):
    """Check if molecule has changed and switch timing context"""
    base_mol = self._get_molecule_name_internal()
    # Determine user-scoped key so changing the publication contact creates a new entry
    try:
      user_display = self._get_current_user_display() or ''
    except Exception:
      user_display = ''
    if user_display:
      mol_name = f"{base_mol} [{user_display}]"
    else:
      mol_name = base_mol
    
    # Periodic auto-save (every 10 seconds)
    if do_autosave and time.time() - self._last_auto_save > self._save_interval:
      if self.current_molecule and self.current_molecule != "No structure loaded":
        self.save_current_molecule_timing(reset_idle=True)
      self._last_auto_save = time.time()
    
    if mol_name != self.current_molecule:
      # Save current molecule timing if exists
      if self.current_molecule and self.current_molecule != "No structure loaded":
        self.save_current_molecule_timing()
      
      # Switch to new molecule
      self.current_molecule = mol_name
      if mol_name != "No structure loaded":
        if mol_name not in self.molecule_timings:
          # New user or new molecule — create fresh entry and attach base sNum
          ui = None
          try:
            ui = self._get_user_info()
          except Exception:
            ui = None
          self.molecule_timings[mol_name] = {
            'total_work_time': 0.0,
            'total_idle_time': 0.0,
            'total_refine_time': 0.0,
            'total_run_time': 0.0,
            'filepath': "",
            'sNum': OV.ModelSrc(),
            'base_sNum': base_mol,
            'user': ui or {},
            'uuid': str(uuid.uuid4()),
            'last_updated': time.strftime('%Y-%m-%d %H:%M:%S')
          }
          self.save_timing_data()
        self.current_start_time = time.time()
        self._session_refine_time = 0.0
        self._reset_idle_tracking(reset_gui=True)
    else:
      # Same molecule, ensure it exists in timings
      if mol_name != "No structure loaded" and mol_name not in self.molecule_timings:
        ui = None
        try:
          ui = self._get_user_info()
        except Exception:
          ui = None
        self.molecule_timings[mol_name] = {
          'total_work_time': 0.0,
          'total_idle_time': 0.0,
          'total_refine_time': 0.0,
          'total_run_time': 0.0,
          'last_updated': time.strftime('%Y-%m-%d %H:%M:%S'),
          'base_sNum': base_mol,
          'user': ui or {}
        }
        self.save_timing_data()
        if self.current_start_time is None:
          self.current_start_time = time.time()
          self._reset_idle_tracking(reset_gui=True)
  
  def save_current_molecule_timing(self, reset_idle=True):
    """Save timing for current molecule"""
    if not self.current_molecule or self.current_molecule == "No structure loaded":
      return
    
    if self.current_start_time is not None:
      elapsed = time.time() - self.current_start_time
      idle = self._get_idle_seconds()
      # Deduct both the listener-tracked refine time and any NoSpher wall-clock time
      wall_refine = self._session_refine_time + getattr(self, '_nospher_wall_clock', 0.0)
      work = max(0, elapsed - idle - wall_refine)
      
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
        # Attach current user info if available and log it to a debug file
        try:
          user_info = self._get_user_info()
          # Collect diagnostic values to aid debugging when resolution fails
          try:
            ctrl_checked = self.read_publication_contact_control() or ''
          except Exception:
            ctrl_checked = ''
          try:
            ctrl_raw = OV.GetControlValue('SET_SNUM_METACIF_OPERATOR') or ''
          except Exception:
            ctrl_raw = ''
          try:
            ctrl_submitter = OV.GetControlValue('SET_SNUM_METACIF_SUBMITTER') or ''
          except Exception:
            ctrl_submitter = ''
          try:
            param_operator = OV.GetParam('snum.report.operator', None) or ''
          except Exception:
            param_operator = ''
          try:
            param_submitter = OV.GetParam('snum.report.submitter', None) or ''
          except Exception:
            param_submitter = ''

          # Attach diagnostics to the user_info for logging
          user_info.setdefault('debug', {})
          user_info['debug'].update({
            'control_checked': str(ctrl_checked),
            'control_raw': str(ctrl_raw),
            'control_submitter': str(ctrl_submitter),
            'param_operator': str(param_operator),
            'param_submitter': str(param_submitter),
          })

          # If no displayname resolved, try direct control/param fallbacks here
          source = 'db' if (user_info.get('displayname')) else None
          if not user_info.get('displayname'):
            try:
              if ctrl_raw and str(ctrl_raw).strip() and str(ctrl_raw) not in ('?', ''):
                user_info['displayname'] = str(ctrl_raw)
                source = 'control'
              elif ctrl_checked and str(ctrl_checked).strip() and str(ctrl_checked) not in ('?', ''):
                user_info['displayname'] = str(ctrl_checked)
                source = 'control'
              elif ctrl_submitter and str(ctrl_submitter).strip() and str(ctrl_submitter) not in ('?', ''):
                user_info['displayname'] = str(ctrl_submitter)
                source = 'control'
            except Exception:
              pass
          if not user_info.get('displayname'):
            try:
              if param_operator and str(param_operator).strip() and str(param_operator) not in ('?', ''):
                user_info['displayname'] = str(param_operator)
                source = 'param'
              elif param_submitter and str(param_submitter).strip() and str(param_submitter) not in ('?', ''):
                user_info['displayname'] = str(param_submitter)
                source = 'param'
            except Exception:
              pass
          if source is None:
            source = 'none'
          user_info['source'] = source
          self.molecule_timings[self.current_molecule].setdefault('user', user_info)
          try:
            logp = os.path.join(instance_path, 'TimerPlus_debug.log')
            with open(logp, 'a') as lf:
              lf.write("%s\t%s\t%s\n" % (time.strftime('%Y-%m-%d %H:%M:%S'), self.current_molecule, json.dumps(user_info)))
          except Exception:
            pass
        except Exception:
          pass
      self.save_timing_data()
      
      # Reset timers to avoid double-counting
      self.current_start_time = time.time()
      if reset_idle:
        self._reset_idle_tracking(reset_gui=True)
      self._session_refine_time = 0.0
      self._nospher_wall_clock = 0.0
  
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

  def _find_nospher_files(self):
    """Return a list of candidate NoSpher output files under the current sNumPath."""
    try:
      matches = []
      mol = (self.current_molecule or '').strip()

      # 1) Search the molecule's working directory (sNumPath)
      base = self.sNumPath
      if base and os.path.exists(base):
        for root, dirs, files in os.walk(base):
          for fn in files:
            if 'nospher' in fn.lower() or (mol and mol.lower() in fn.lower() and 'nospher' in fn.lower()):
              matches.append(os.path.join(root, fn))

      # 2) Check the central DataDir 'samples/<mol>/' location (explicit location you provided)
      try:
        samples_dir = os.path.join(instance_path, 'samples', mol)
        if os.path.exists(samples_dir):
          for fn in os.listdir(samples_dir):
            if 'nospher' in fn.lower() or (mol and mol.lower() in fn.lower()):
              matches.append(os.path.join(samples_dir, fn))
      except Exception:
        pass

      # 3) Fallback: search entire DataDir for files mentioning nospher (avoid deep recursion unless needed)
      try:
        data_samples = os.path.join(instance_path, 'samples')
        if os.path.exists(data_samples):
          for root, dirs, files in os.walk(data_samples):
            for fn in files:
              if 'nospher' in fn.lower() or (mol and mol.lower() in fn.lower()):
                matches.append(os.path.join(root, fn))
      except Exception:
        pass

      # Deduplicate and return
      unique = []
      seen = set()
      for p in matches:
        if p not in seen:
          seen.add(p)
          unique.append(p)
      return unique
    except Exception:
      return []

  def _parse_execution_time_from_file(self, filepath):
    """Parse the duration of the most recent refinement from a NoSpher output file."""
    try:
      with open(filepath, 'r', errors='ignore') as f:
        content = f.read(100000)

      ts_pat = r'(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:\.\d+)?)'
      start_re = re.compile(r'Refinement\s+start\w*\s+at:\s*' + ts_pat, re.I)
      finish_re = re.compile(r'Refinement\s+finish\w*\s+at:\s*' + ts_pat, re.I)

      def _parse_ts(s):
        s = s.strip()
        for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S'):
          try:
            return datetime.strptime(s, fmt)
          except Exception:
            pass
        return None

      starts = [(m.start(), m.group(1)) for m in start_re.finditer(content)]
      finishes = [(m.start(), m.group(1)) for m in finish_re.finditer(content)]
      print('TimerPlus: found %d start(s), %d finish(es) in %s' % (
        len(starts), len(finishes), os.path.basename(filepath)))

      # Return only the LAST finish/start pair (most recent refinement)
      for fpos, fstr in reversed(finishes):
        f_dt = _parse_ts(fstr)
        if f_dt is None:
          continue
        for spos, sstr in reversed(starts):
          if spos < fpos:
            s_dt = _parse_ts(sstr)
            if s_dt is not None:
              delta = (f_dt - s_dt).total_seconds()
              print('TimerPlus: last pair: %s -> %s = %.3fs' % (
                sstr.strip(), fstr.strip(), delta))
              if delta >= 0:
                return float(delta)
            break  # tried the closest start, no valid pair
    except Exception as e:
      print('TimerPlus: _parse_execution_time_from_file error:', e)
    return None

  def _get_nospher_refine_time_for_current(self):
    """Find the most recent NoSpher output for the current molecule and parse its execution time."""
    try:
      mol = self.current_molecule
      if not mol or mol == 'No structure loaded':
        return None
      files = self._find_nospher_files()
      if not files:
        print('TimerPlus: NoSpher search found no candidate files for', mol)
        return None
      # Score files so that explicit NoSpher outputs (e.g. "mol.NoSpherA2")
      # are preferred over generic history files, then fall back to mtime.
      def score_fp(fp):
        bn = os.path.basename(fp).lower()
        s = 0
        # exact pattern like "<mol>.nospher" (or with suffix) gets highest priority
        if bn.startswith(mol.lower() + '.nospher'):
          s += 20
        # files that contain 'nospher' get moderate priority
        if 'nospher' in bn:
          s += 10
        # if filename mentions molecule anywhere, small boost
        if mol.lower() in bn:
          s += 2
        # include modification time as tiebreaker (seconds since epoch)
        try:
          mtime = os.path.getmtime(fp)
        except Exception:
          mtime = 0
        return (s, mtime)

      files = sorted(files, key=lambda p: score_fp(p), reverse=True)
      for fp in files:
        name_ok = mol.lower() in os.path.basename(fp).lower()
        try:
          parsed = self._parse_execution_time_from_file(fp)
        except Exception:
          parsed = None
        print('TimerPlus: checked NoSpher file:', fp, 'name_ok=', name_ok, 'parsed=', parsed)
        if name_ok and parsed and parsed > 0:
          return parsed
      return None
    except Exception:
      return None

  def _scan_and_apply_nospher(self):
    """Locate a NoSpher output, parse execution time and apply to current molecule timing."""
    try:
      # Precondition: only run NoSpher parsing if user enabled NoSpherA2 in refine settings
      try:
        nospher_enabled = OV.GetParam('snum.NoSpherA2.use_aspherical', False)
      except Exception:
        nospher_enabled = False
      if not nospher_enabled:
        print('TimerPlus: _scan_and_apply_nospher -> NoSpherA2 not enabled, skipping')
        return None

      parsed = self._get_nospher_refine_time_for_current()
      if parsed is None:
        print('TimerPlus: _scan_and_apply_nospher -> no parsed time found')
        return None
      mol = self.current_molecule
      if not mol or mol == 'No structure loaded':
        print('TimerPlus: _scan_and_apply_nospher -> no current molecule')
        return None

      # Guard with file mtime so each refinement run is counted exactly once.
      # Find the file mtime of the best candidate (same lookup as parsing).
      files = self._find_nospher_files()
      current_mtime = 0.0
      if files:
        def _score(fp):
          bn = os.path.basename(fp).lower()
          s = 20 if bn.startswith(mol.lower() + '.nospher') else (10 if 'nospher' in bn else 0)
          s += 2 if mol.lower() in bn else 0
          try:
            return (s, os.path.getmtime(fp))
          except Exception:
            return (s, 0)
        best = max(files, key=_score)
        try:
          current_mtime = os.path.getmtime(best)
        except Exception:
          current_mtime = 0.0

      if mol not in self.molecule_timings:
        self.molecule_timings[mol] = {}
      last_mtime = float(self.molecule_timings[mol].get('last_nospher_mtime', 0.0))
      if current_mtime <= last_mtime:
        print('TimerPlus: _scan_and_apply_nospher -> file not newer (mtime=%.3f, last=%.3f), skipping' % (current_mtime, last_mtime))
        return None

      # New refinement result — add its duration to the running total.
      old_refine = float(self.molecule_timings[mol].get('total_refine_time', 0.0))
      self.molecule_timings[mol]['total_refine_time'] = old_refine + float(parsed)
      self.molecule_timings[mol]['last_nospher_mtime'] = current_mtime
      self.molecule_timings[mol]['last_updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
      # This refinement accumulated as idle (no mouse movement); correct it.
      self._idle_seconds = max(0.0, self._idle_seconds - parsed)
      old_stored_idle = float(self.molecule_timings[mol].get('total_idle_time', 0.0))
      self.molecule_timings[mol]['total_idle_time'] = max(0.0, old_stored_idle - parsed)
      print('TimerPlus: corrected idle by -%.3fs (refine duration)' % parsed)
      try:
        self.save_timing_data()
      except Exception as e:
        print('TimerPlus: saving timing data failed:', e)
      # Save the wall-clock duration separately so work deduction stays correct,
      # then zero out _session_refine_time so the display never double-counts
      # (total_refine_time from the file is already the authoritative value).
      self._nospher_wall_clock = float(self._session_refine_time)
      self._session_refine_time = 0.0
      print('TimerPlus: applied NoSpher refine time for %s -> %.3f seconds' % (mol, parsed))
      return parsed
    except Exception as e:
      print('TimerPlus: error in _scan_and_apply_nospher:', e)
      return None

  def _retry_nospher(self):
    """Called by olx.Schedule a few seconds after refine end to parse NoSpher output."""
    try:
      print("TimerPlus: _retry_nospher -> invoking _scan_and_apply_nospher()")
      self._scan_and_apply_nospher()
    except Exception as e:
      print("TimerPlus: _retry_nospher failed:", e)

  def test_parse_file(self, filepath):
    """Diagnostic: parse a specific file and return extracted seconds (or None)."""
    try:
      parsed = self._parse_execution_time_from_file(filepath)
      print('TimerPlus: test_parse_file ->', filepath, '=>', parsed)
      return parsed
    except Exception as e:
      print('TimerPlus: test_parse_file error for', filepath, ':', e)
      return None

  def print_formula(self):
    self.check_and_switch_molecule(do_autosave=False)
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
    self.check_and_switch_molecule(do_autosave=False)
    try:
      if self.current_molecule == "No structure loaded" or self.current_molecule is None:
        return 0.0
      current_idle = self._get_idle_seconds()
      total_idle = self.molecule_timings.get(self.current_molecule, {}).get('total_idle_time', 0.0)
      return round(float(total_idle + current_idle), 1)
    except Exception:
      return 0.0
  
  def get_work_time(self):
    """Get work time for current molecule"""
    self.check_and_switch_molecule(do_autosave=False)
    try:
      if self.current_molecule == "No structure loaded" or self.current_molecule is None:
        return 0.0
      if self.current_start_time is None:
        self.current_start_time = time.time()
        self._reset_idle_tracking(reset_gui=True)
        return 0.0
      elapsed = time.time() - self.current_start_time
      idle = self._get_idle_seconds()
      session_refine = self._session_refine_time
      work = max(0, elapsed - idle - session_refine)
      total_work = self.molecule_timings.get(self.current_molecule, {}).get('total_work_time', 0.0)
      result = round(float(total_work + work), 1)
      return result
    except Exception:
      return 0.0
  
  def get_running_time(self):
    """Get running time for current molecule"""
    self.check_and_switch_molecule(do_autosave=False)
    try:
      if self.current_molecule == "No structure loaded" or self.current_molecule is None:
        return 0.0
      if self.current_start_time is None:
        # Timer not started yet, initialize it
        self.current_start_time = time.time()
        self._reset_idle_tracking(reset_gui=True)
        return 0.0
      elapsed = time.time() - self.current_start_time
      total_run = self.molecule_timings.get(self.current_molecule, {}).get('total_run_time', 0.0)
      return round(float(total_run + elapsed), 1)
    except Exception:
      return 0.0
  
  def get_molecule_name(self):
    """Get the current structure/molecule name"""
    self.check_and_switch_molecule(do_autosave=False)
    return self.current_molecule if self.current_molecule else "No structure loaded"
  
  def update_timing(self):
    """Force update and save current molecule timing"""
    self._mark_activity()
    self.check_and_switch_molecule()
    self.save_current_molecule_timing()
    return "Timing saved and updated"
  
  def update_timer_vars(self, push_controls=True):
    self.check_and_switch_molecule(do_autosave=False)
    mol = self.current_molecule
    if not mol or mol == "No structure loaded":
      return
    OV.SetVar('TIMER_MOL', mol)
    if push_controls:
      try:
        OV.SetControlValue('TIMER_MOL', mol)
      except Exception:
        pass
    # Provide current user displayname for the UI
    try:
      user_display = ''
      stored = self.molecule_timings.get(mol, {}).get('user', {})
      if stored and stored.get('displayname'):
        user_display = stored.get('displayname')
      else:
        try:
          user_display = self._get_user_info().get('displayname') or ''
        except Exception:
          user_display = ''
      OV.SetVar('TIMER_USER', user_display)
      if push_controls:
        try:
          OV.SetControlValue('TIMER_USER', user_display)
        except Exception:
          pass
    except Exception:
      pass
    elapsed = 0.0
    if self.current_start_time is not None:
      elapsed = max(0.0, time.time() - self.current_start_time)
    current_idle = self._get_idle_seconds()

    try:
      nospher_enabled = bool(OV.GetParam('snum.NoSpherA2.use_aspherical', False))
    except Exception:
      nospher_enabled = False
    # When NoSpher is enabled, total_refine_time is authoritative (set from the file)
    # and _session_refine_time has been zeroed out in _scan_and_apply_nospher.
    # For work, deduct both session refine and any NoSpher wall-clock.
    wall_refine = self._session_refine_time + getattr(self, '_nospher_wall_clock', 0.0)
    totals = {
      'WORK': self.molecule_timings[mol].get('total_work_time', 0.0) + max(0.0, elapsed - current_idle - wall_refine),
      'REFINE': self.molecule_timings[mol].get('total_refine_time', 0.0) + self._session_refine_time,
      'IDLE': self.molecule_timings[mol].get('total_idle_time', 0.0) + current_idle,
      'RUN': self.molecule_timings[mol].get('total_run_time', 0.0) + elapsed,
    }

    for item, seconds in totals.items():
      t = self._format_time(seconds)
      ctrl = f'TIMER_{item}'
      OV.SetVar(ctrl, t)
      if push_controls:
        try:
          OV.SetControlValue(ctrl, t)
        except Exception:
          pass

  def refresh_display(self):
    """Refresh the display to show current timing"""
    self.check_and_switch_molecule(do_autosave=False)
    self.update_timer_vars(push_controls=True)
    olx.html.Update()
    return "Display refreshed"
  
  def reset_current_timing(self):
    """Reset timing for current molecule"""
    self._mark_activity()
    mol_name = self._get_molecule_name_internal()
    if mol_name and mol_name != "No structure loaded":
      if mol_name in self.molecule_timings:
        del self.molecule_timings[mol_name]
        self.save_timing_data()
      self.current_start_time = time.time()
      self.current_idle_start = 0
      self._reset_idle_tracking(reset_gui=True)
      try:
        olx.html.Update()
      except:
        pass
      return "Timing reset for %s" % mol_name
    return "No structure loaded"
  
  def get_timing_history(self):
    """Get formatted HTML table of timing history for all molecules"""
    self.check_and_switch_molecule(do_autosave=False)
    
    # Get current session times
    current_work = 0.0
    current_idle = 0.0
    current_total = 0.0
    
    if self.current_molecule and self.current_molecule != "No structure loaded" and self.current_start_time is not None:
      elapsed = time.time() - self.current_start_time
      idle = self._get_idle_seconds()
      current_work = max(0, elapsed - idle - self._session_refine_time)
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
  
  def show_history(self):
    """Open a popup window showing the full timing history."""
    try:
      # Popup a simple HTML page bundled with the plugin that displays the history
      wFilePath = os.path.join(self.p_path, 'timerplus_history.htm')
      # Use a named popup so multiple calls reuse the same window
      try:
        olx.Popup('timerplus_history', wFilePath, b="tcr", t="TimerPlus History", w=800, h=500)
      except Exception:
        # Fallback to simple popup call without extra args
        olx.Popup('timerplus_history', wFilePath)
    except Exception as e:
      print("TimerPlus: could not open history popup: %s" % str(e))
  
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
  
