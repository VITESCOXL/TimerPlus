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
import html as html_lib
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
    self._history_search_term = ""  # For filtering in history popup
    self._history_modal_open = False
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
    self._idle_grace = float(OV.GetParam('TimerPlus.idle_grace', 60.0) or 60.0)
    self._last_mouse_pos = None
    
    OV.registerFunction(self.print_formula,True,self.p_name)
    OV.registerFunction(self.get_idle_time,True,self.p_name)
    OV.registerFunction(self.get_work_time,True,self.p_name)
    OV.registerFunction(self.get_running_time,True,self.p_name)
    OV.registerFunction(self.get_molecule_name,True,self.p_name)
    OV.registerFunction(self.get_timing_history,True,self.p_name)
    OV.registerFunction(self.get_timing_history_popup,True,self.p_name)
    OV.registerFunction(self.get_filtered_history_popup,True,self.p_name)
    OV.registerFunction(self.filter_history_display,True,self.p_name)
    OV.registerFunction(self.clear_history_filter,True,self.p_name)
    OV.registerFunction(self.show_search_results,True,self.p_name)
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
    OV.registerFunction(self.edit_history, True, self.p_name)
    OV.registerFunction(self.get_timing_edit_form, True, self.p_name)
    OV.registerFunction(self.update_history, True, self.p_name)
    OV.registerFunction(self.edit_history_for, True, self.p_name)
    OV.registerFunction(self.update_history_from_popup, True, self.p_name)
    OV.registerFunction(self.set_edit_work, True, self.p_name)
    OV.registerFunction(self._prefill_edit_popup, True, self.p_name)
    OV.registerFunction(self.open_molecule, True, self.p_name)
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
    for _var in ('TIMER_MOL', 'TIMER_WORK', 'TIMER_REFINE', 'TIMER_IDLE', 'TIMER_RUN', 'TIMER_WFN', 'TIMER_USER'):
      OV.SetVar(_var, '')
    self.update_timer_vars()

    # Patch multiple datasets removed (was optional UI badge)

    # Start recurring display refresh based on phil refresh_interval (Currently inactive)
    # self._start_refresh_timer()

  # _patch_multiple_dataset removed — optional GUI badge feature disabled

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
    except Exception:
      pass


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

    # No parameter fallbacks remain; nothing more to resolve here

    return info

  def _get_current_user_display(self):
    """Return a short display name for the current GUI publication contact, or empty string."""
    try:
      ui = self._get_user_info()
      if ui and ui.get('displayname'):
        return str(ui.get('displayname'))
      # Do not fallback to raw GUI control strings here
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
  
  def save_timing_data(self, molecules=None):
    """
    Save the timing history.

    The central history (DataDir()/TimerPlus_history.json) holds every
    molecule; each molecule also has its own <name>_timer.json in its
    structure folder's olex2/ subfolder. Only the per-molecule files of `molecules`
    are written -- by default just the current molecule: the others have
    not changed, and rewriting them all (137 molecules in 56 folders, every
    10 s) changed the modified date of every one of those folders.
    """
    try:
      if self.current_molecule in self.molecule_timings:
        self._ensure_user_info(self.current_molecule, self.molecule_timings[self.current_molecule])
    except Exception as e:
      print("TimerPlus: could not resolve user info: %s" % e)

    # Central history: atomic (temp file + rename) -- it lives in DataDir(),
    # whose date doesn't matter -- and only when its content changed.
    try:
      text = json.dumps(self.molecule_timings, indent=2, ensure_ascii=False, default=str)
      if text != getattr(self, '_last_history_text', None):
        tmp_fn = self.timing_data_file + '.tmp'
        try:
          with open(tmp_fn, 'w', encoding='utf-8') as f:
            f.write(text)
          try:
            os.replace(tmp_fn, self.timing_data_file)
          except Exception:
            # Fallback to rename if replace not available
            try:
              os.remove(self.timing_data_file)
            except Exception:
              pass
            os.rename(tmp_fn, self.timing_data_file)
          self._last_history_text = text
        except Exception as e:
          print("Error saving timing data: %s" % str(e))
          try:
            if os.path.exists(tmp_fn):
              os.remove(tmp_fn)
          except Exception:
            pass
    except Exception as e:
      print("Error saving timing data: %s" % str(e))

    if molecules is None:
      molecules = [self.current_molecule]
    for mol_name in molecules:
      mol_data = self.molecule_timings.get(mol_name)
      if mol_data is None:
        continue
      try:
        self._write_molecule_file(mol_name, mol_data)
      except Exception as e:
        print("Error saving local timing data for %s: %s" % (mol_name, str(e)))

  def _ensure_user_info(self, mol_name, mol_data):
    """Fill in the user of the current molecule if it is still missing."""
    if mol_data.get('user') and mol_data.get('user', {}).get('displayname'):
      return
    if mol_name != self.current_molecule:
      return
    ui = self._get_user_info()
    if ui and (ui.get('displayname') or ui.get('id')):
      mol_data['user'] = ui

  def _molecule_file(self, folder, mol_name):
    """Path of <name>_timer.json in folder/olex2, or None if that folder
    does not exist (it is never created here)."""
    if not folder:
      return None
    strdir = os.path.join(folder, 'olex2')
    if not os.path.isdir(strdir):
      return None
    return os.path.join(strdir, '%s_timer.json' % self._sanitize_for_filename(mol_name))

  def _write_molecule_file(self, mol_name, mol_data):
    """
    Write <name>_timer.json into the olex2/ subfolder of the molecule's
    structure folder -- only if its content changed, and in place.

    TimerPlus must never change the modified date of a structure folder.
    A folder's date changes when a file in it is created, deleted or
    renamed (not when an existing file is rewritten), so nothing is ever
    created in the structure folder itself: olex2/ is Olex2's own working
    folder, which it updates all the time anyway. If there is no olex2/
    folder, no local file is written (the central history has the data).
    """
    fn = self._molecule_file(mol_data.get('sNumPath'), mol_name)
    if not fn:
      return
    text = json.dumps(mol_data, indent=2, ensure_ascii=False, default=str)
    written = self.__dict__.setdefault('_written_molecule_files', {})
    if written.get(fn) == text:
      return
    if os.path.exists(fn):
      try:
        with open(fn, 'r', encoding='utf-8') as f:
          if f.read() == text:
            written[fn] = text
            return
      except Exception as e:
        print("TimerPlus: could not read %s: %s" % (fn, e))
    with open(fn, 'w', encoding='utf-8') as f:
      f.write(text)
    written[fn] = text


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
      pass

  def _on_file_changed(self, filetype):
    """Called automatically by Olex2 whenever a structure is opened."""
    try:
      self._mark_activity()
      self.check_and_switch_molecule()
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
      pass

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
      olx.Schedule(3, "spy.TimerPlus.refresh_display()")  # Update display immediately after refine ends
      # After a refine ends, attempt to parse NoSpher output.
      # Schedule a retry 4 s later so the file has time to be fully written.
      try:
        pass
        olx.Schedule(4, "spy.TimerPlus._retry_nospher()")
      except Exception as e:
        pass
        try:
          self._scan_and_apply_nospher()
        except Exception as e2:
          pass
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

  # def _start_refresh_timer(self):
  #   """Schedule recurring display refresh using olx.Schedule. CURRENTLY INACTIVE"""
  #   try:
  #     interval = int(OV.GetParam('TimerPlus.refresh_interval', 30))
  #   except Exception:
  #     interval = 30
  #   if interval <= 0:
  #     self._stop_refresh_timer()
  #     return
  #   self._refresh_interval = interval
  #   self._refresh_active = True
  #   olx.Schedule(interval, "spy.TimerPlus._tick()")

  def _tick(self):
    """Called by olx.Schedule"""
    if not self._refresh_active:
      return
    try:
      # Idle is now plugin-side, so scheduled control updates are safe again.
      self._sample_pointer_activity()
      self.check_and_switch_molecule()
      self.update_timer_vars(push_controls=True)
      self._post_work_time_notification()
      try:
        olx.html.Update()
      except Exception:
        pass
    except Exception:
      pass
    # if self._refresh_active and self._refresh_interval and self._refresh_interval > 0:
    #   olx.Schedule(self._refresh_interval, "spy.TimerPlus._tick()")

  _work_note_re = re.compile(r'(?:\s*\|\s*)?Work: (?:\d+(?::\d+)+|(?:\d+h)?(?:\d+min)?(?:\d+s)?)')

  def _format_duration(self, seconds):
    """'3h23min' (or '3h23min05s' at 'second' resolution), so that it reads
    as a time; leading zero units are left out ('14min', '0min')."""
    seconds = int(seconds)
    h, m, sec = seconds // 3600, seconds % 3600 // 60, seconds % 60
    s = '%dh' % h if h else ''
    if str(OV.GetParam('TimerPlus.time_resolution', 'minute')).lower() == 'second':
      if h or m:
        s += '%02dmin' % m if h else '%dmin' % m
        return s + '%02ds' % sec
      return '%ds' % sec
    return s + ('%02dmin' % m if h else '%dmin' % m)

  def _post_work_time_notification(self):
    """Show the current molecule's total work time in Olex2's notification
    bar at the top of the screen. The bar is shared (refinement results,
    "... is loaded"), so the work time is appended to whatever message is
    showing ("... | Work: 01:23") and only that part is replaced on later
    updates. The caller updates the HTML."""
    try:
      cur = OV.GetVar('GuiNotification', '') or ''
      # stored as "<font color=..>text;bg;fg </font>" -- text before the first ';'
      cut = cur.find(';')
      if cut < 0:
        cut = cur.rfind('</font>')
      if cut < 0:
        cut = len(cur)
      head = self._work_note_re.sub('', cur[:cut])
      tail = cur[cut:]
      note = ''
      if (OV.GetParam('TimerPlus.notify_work_time', True)
          and self.current_molecule and self.current_molecule != "No structure loaded"):
        note = 'Work: %s' % self._format_duration(self.get_work_time())
        if re.sub(r'<[^>]*>', '', head).strip():
          note = ' | ' + note
      if not cur:
        new = '<font color=%s>%s </font>' % (OV.GetVar('gui.grey', '#888888'), note) if note else ''
      else:
        new = head + note + tail
      if new != cur:
        OV.SetVar('GuiNotification', new)
    except Exception as e:
      print("TimerPlus: could not update the notification: %s" % e)

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

      # Count activity while the pointer is anywhere inside the Olex2 window
      # (GUI panel, console, molecule) -- not only over the molecule, which
      # left almost all real work counted as idle.
      ws = [int(v) for v in olx.GetWindowSize('main').split(',')]
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
    """Return formatted time since Olex2 (the plugin) was launched."""
    try:
      return self._format_time(time.time() - self.session_start_time)
    except:
      return self._format_time(0)

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

      # Fallback to GUI controls is temporarily disabled during testing.
      # Returning None here ensures only CIF-backed values are used.
      # To re-enable live GUI fallbacks, restore the OV.GetControlValue calls.
      # val = None
      # val2 = None
      pass
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
          self._record_location(self.molecule_timings[mol_name])
          self.save_timing_data()
        else:
          self._record_location(self.molecule_timings[mol_name])
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
        self._record_location(self.molecule_timings[mol_name])
        self.save_timing_data()
        if self.current_start_time is None:
          self.current_start_time = time.time()
          self._reset_idle_tracking(reset_gui=True)
  
  def _record_location(self, rec):
    """Store where the molecule that has just become current lives: its
    folder, file and -- for a CIF -- data block, so that the history can
    reopen it. Called on a switch, when Olex2's FilePath() etc. belong to
    this molecule (and before the previous one is saved, which used to give
    a briefly opened structure the folder of the next one)."""
    try:
      rec['sNumPath'] = self.sNumPath
      rec['sNum'] = self.sNum
      rec['filepath'] = OV.FileFull()
      rec.pop('data_block', None)
      if olx.IsFileType('cif') == 'true':
        rec['data_block'] = int(olx.xf.CurrentData())
    except Exception as e:
      print("TimerPlus: could not record the location of the structure: %s" % e)

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
        self.molecule_timings[self.current_molecule].setdefault('uuid', str(uuid.uuid4()))
        # Attach current user info if available (DB/CIF only; no literal fallbacks)
        try:
          user_info = self._get_user_info()
          try:
            if user_info is None:
              user_info = {}
            user_info['source'] = 'db' if user_info.get('displayname') else 'none'
            self.molecule_timings[self.current_molecule].setdefault('user', user_info)
            try:
              logp = os.path.join(instance_path, 'TimerPlus_debug.log')
              with open(logp, 'a') as lf:
                lf.write("%s\t%s\t%s\n" % (time.strftime('%Y-%m-%d %H:%M:%S'), self.current_molecule, json.dumps(user_info)))
            except Exception:
              pass
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
              if delta >= 0:
                return float(delta)
            break  # tried the closest start, no valid pair
    except Exception as e:
      pass
    return None

  def _get_nospher_refine_time_for_current(self):
    """Find the most recent NoSpher output for the current molecule and parse its execution time."""
    try:
      mol = self.current_molecule
      if not mol or mol == 'No structure loaded':
        return None
      files = self._find_nospher_files()
      if not files:
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
        return None

      parsed = self._get_nospher_refine_time_for_current()
      if parsed is None:
        return None
      mol = self.current_molecule
      if not mol or mol == 'No structure loaded':
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
      try:
        self.save_timing_data([mol])
      except Exception as e:
        pass
      # Save the wall-clock duration separately so work deduction stays correct,
      # then zero out _session_refine_time so the display never double-counts
      # (total_refine_time from the file is already the authoritative value).
      self._nospher_wall_clock = float(self._session_refine_time)
      self._session_refine_time = 0.0
      return parsed
    except Exception as e:
      return None

  def _retry_nospher(self):
    """Called by olx.Schedule a few seconds after refine end to parse NoSpher output."""
    try:
      self._scan_and_apply_nospher()
    except Exception as e:
      pass

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

    try:
      wfn_t = self._get_wavefunction_time_str(mol, self.molecule_timings.get(mol, {}))
    except Exception:
      wfn_t = 'N/A'
    OV.SetVar('TIMER_WFN', wfn_t)
    if push_controls:
      try:
        OV.SetControlValue('TIMER_WFN', wfn_t)
      except Exception:
        pass

  def refresh_display(self):
    """Refresh the display to show current timing"""
    self.check_and_switch_molecule(do_autosave=False)
    self.update_timer_vars(push_controls=True)
    self._post_work_time_notification()
    olx.html.Update()
    return "Display refreshed"
  
  def reset_current_timing(self):
    """Reset timing for current molecule"""
    self._mark_activity()
    # Ensure we use the same molecule key format as stored in `molecule_timings`
    self.check_and_switch_molecule(do_autosave=False)
    mol_name = self.current_molecule
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
    
    # Add all saved molecules (skip empty or placeholder keys)
    for mol_name, data in self.molecule_timings.items():
      try:
        if not mol_name or str(mol_name).strip() == '' or mol_name == "No structure loaded":
          continue
      except Exception:
        continue
      updated = data.get('last_updated', 'Unknown')
      try:
        s = str(updated).strip()
        if s and s not in ('Unknown', 'Active Now'):
          dt = None
          try:
            dt = datetime.fromisoformat(s)
          except Exception:
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
              try:
                dt = datetime.strptime(s, fmt)
                break
              except Exception:
                pass
          if dt is not None:
            updated = dt.strftime('%Y-%m-%d %H:%M:%S')
      except Exception:
        pass
      molecules_to_show[mol_name] = {
        'work': data.get('total_work_time', 0.0),
        'refine': data.get('total_refine_time', 0.0),
        'idle': data.get('total_idle_time', 0.0),
        'total': data.get('total_run_time', 0.0),
        'updated': updated,
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
      return "<tr><td colspan='5' style='text-align:center;'>No timing data available.<br/>Load a structure to start tracking.</td></tr>"
    
    html_rows = []
    # Sort by current first, then by last updated
    sorted_molecules = sorted(
      molecules_to_show.items(),
      key=lambda x: (not x[1]['is_current'], x[1]['updated'] if x[1]['updated'] != "Active Now" else "9999"),
      reverse=True
    )
    
    # Limit to 5 latest structures
    sorted_molecules = sorted_molecules[:5]
    
    for mol_name, data in sorted_molecules:
      work_str = self._format_time(data['work'])
      refine_str = self._format_time(data['refine'])
      wfn_str = self._get_wavefunction_time_str(mol_name, data)
      
      # Highlight current molecule
      bg_color = "#e8f4f8" if data['is_current'] else "#ffffff"

      # Escape single quotes for the spy call
      safe_name = mol_name.replace("\\", "\\\\").replace("'", "\\'")
      name_link = "<a href=\"spy.TimerPlus.open_molecule('%s')\"><b>%s</b></a>" % (
        safe_name, self._short_name(mol_name))
      html_rows.append(
        "<tr style='background-color: %s;'>" % bg_color +
        "<td width='34%%' style='padding:6px;'>%s</td>" % name_link +
        "<td width='12%%' style='padding:6px; text-align:center;'>%s</td>" % work_str +
        "<td width='12%%' style='padding:6px; text-align:center;'>%s</td>" % refine_str +
        "<td width='12%%' style='padding:6px; text-align:center;'>%s</td>" % wfn_str +
        "<td width='30%%' style='padding:6px; text-align:center;'>%s</td>" % self._short_date(data['updated']) +
        "</tr>"
      )
    
    return "\n".join(html_rows)

  def _short_name(self, name, max_len=30):
    """Shorten a long molecule name to 'head...tail', cutting at underscores
    where possible: 'm_C1-9exp_exp_3639_exp_3644_exp_3642_more' becomes
    'm_C1-9exp..._more'."""
    name = str(name)
    if len(name) <= max_len:
      return name
    budget = max_len - 3
    parts = name.split('_')
    if len(parts) > 2:
      # tail: last part(s), at least a few characters
      i = len(parts) - 1
      tail = '_' + parts[i]
      while i > 2 and len(tail) < 5 and len(tail) + 1 + len(parts[i - 1]) <= budget // 2:
        i -= 1
        tail = '_' + parts[i] + tail
      # head: leading parts, as many as fit
      head = parts[0]
      for p in parts[1:i]:
        if len(head) + 1 + len(p) + len(tail) > budget:
          break
        head += '_' + p
      if len(head) >= budget // 3 and len(head) + len(tail) <= budget:
        return head + '...' + tail
    n_tail = max_len // 3
    return name[:max_len - n_tail - 3] + '...' + name[-n_tail:]

  def _molecule_name_link(self, mol_name):
    """Render a molecule name as a link matching the TimerPlus Extras view."""
    safe_name = str(mol_name).replace("\\", "\\\\").replace("'", "\\'")
    safe_name = safe_name.replace("\r", "\\r").replace("\n", "\\n")
    href = html_lib.escape(
      "spy.TimerPlus.open_molecule('%s')" % safe_name, quote=True)
    display_name = html_lib.escape(self._short_name(mol_name))
    return "<a href=\"%s\"><b>%s</b></a>" % (href, display_name)

  def _short_date(self, updated):
    """'2026-10-05 16:11:17' -> 'Mon 5th Oct, 16:11'; the year is added
    when it is not the current one. Other strings (e.g. 'Active Now') are
    returned unchanged."""
    try:
      dt = datetime.strptime(str(updated), '%Y-%m-%d %H:%M:%S')
    except ValueError:
      return updated
    day = dt.day
    if 10 <= day % 100 <= 20:
      suffix = 'th'
    else:
      suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
    s = '%s %d%s %s' % (dt.strftime('%a'), day, suffix, dt.strftime('%b'))
    if dt.year != datetime.now().year:
      s += ' %d' % dt.year
    return s + dt.strftime(', %H:%M')

  def open_molecule(self, mol_name):
    """Open a molecule from the history: the file (and CIF data block) it
    was opened from; for entries recorded before that was stored, its
    <sNum>.res/.ins/.cif in its folder or in a sibling folder named <sNum>
    (older entries may carry the folder of the structure opened next);
    failing all that, the folder itself."""
    data = self.molecule_timings.get(mol_name) or {}
    fn = data.get('filepath') or ''
    if fn and os.path.isfile(fn):
      if data.get('data_block') is not None:
        olex.m('reap "%s#%s"' % (fn, data['data_block']))
      else:
        olex.m('reap "%s"' % fn)
      return
    folder = data.get('sNumPath') or ''
    sNum = data.get('sNum') or data.get('base_sNum') or mol_name
    if not folder or not os.path.isdir(folder):
      print("TimerPlus: no folder known for '%s'" % mol_name)
      return
    for d in (folder, os.path.join(os.path.dirname(folder), sNum)):
      for ext in ('res', 'ins', 'cif'):
        fn = os.path.join(d, '%s.%s' % (sNum, ext))
        if os.path.exists(fn):
          olex.m('reap "%s"' % fn)
          return
    print("TimerPlus: no %s.res/.ins/.cif in %s -- opening the folder" % (sNum, folder))
    olex.m('shell "%s"' % folder)

  def get_timing_history_popup(self):
    """Get formatted HTML table of ALL timing history for popup (no 5-item limit)"""
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
    
    # Add all saved molecules (skip empty or placeholder keys)
    for mol_name, data in self.molecule_timings.items():
      try:
        if not mol_name or str(mol_name).strip() == '' or mol_name == "No structure loaded":
          continue
      except Exception:
        continue
      updated = data.get('last_updated', 'Unknown')
      try:
        s = str(updated).strip()
        if s and s not in ('Unknown', 'Active Now'):
          dt = None
          try:
            dt = datetime.fromisoformat(s)
          except Exception:
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
              try:
                dt = datetime.strptime(s, fmt)
                break
              except Exception:
                pass
          if dt is not None:
            updated = dt.strftime('%Y-%m-%d %H:%M:%S')
      except Exception:
        pass
      molecules_to_show[mol_name] = {
        'work': data.get('total_work_time', 0.0),
        'refine': data.get('total_refine_time', 0.0),
        'idle': data.get('total_idle_time', 0.0),
        'total': data.get('total_run_time', 0.0),
        'updated': updated,
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
      return "<tr><td colspan='7' style='text-align:center;'>No timing data available.<br/>Load a structure to start tracking.</td></tr>"
    
    html_rows = []
    # Sort by current first, then by last updated
    sorted_molecules = sorted(
      molecules_to_show.items(),
      key=lambda x: (not x[1]['is_current'], x[1]['updated'] if x[1]['updated'] != "Active Now" else "9999"),
      reverse=True
    )
    
    # NO 5-item limit for popup - show all
    
    for mol_name, data in sorted_molecules:
      work_str = self._format_time(data['work'])
      refine_str = self._format_time(data['refine'])
      idle_str = self._format_time(data['idle'])
      wfn_str = self._get_wavefunction_time_str(mol_name, data)
      
      # Highlight current molecule
      bg_color = "#e8f4f8" if data['is_current'] else "#ffffff"
      
      # Escape single quotes for JS call
      safe_name = mol_name.replace("\\", "\\\\").replace("'", "\\'")
      name_link = self._molecule_name_link(mol_name)
      edit_link = "<a href=\"spy.TimerPlus.edit_history_for('%s')\">Edit</a>" % safe_name
      html_rows.append(
        "<tr style='background-color: %s;'>" % bg_color +
        "<td width='18%%' style='padding:6px;'>%s</td>" % name_link +
        "<td width='12%%' style='padding:6px; text-align:center;'>%s</td>" % work_str +
        "<td width='12%%' style='padding:6px; text-align:center;'>%s</td>" % refine_str +
        "<td width='12%%' style='padding:6px; text-align:center;'>%s</td>" % idle_str +
        "<td width='14%%' style='padding:6px; text-align:center;'>%s</td>" % wfn_str +
        "<td width='10%%' style='padding:6px; text-align:center;'>%s</td>" % data['updated'] +
        "<td width='22%%' style='padding:6px; text-align:center;'>%s</td>" % edit_link +
        "</tr>"
      )
    
    return "\n".join(html_rows)
  
  def _get_history_popup_layout(self):
    """Return popup dimensions and left/right positions within the Olex2 window."""
    try:
      window = [int(value) for value in olx.GetWindowSize().split(',')]
      origin_x, origin_y, area_width, area_height = window[:4]
      popup_width = max(240, min(800, (area_width - 36) // 2))
      popup_height = max(240, min(500, area_height - 80))
      top = origin_y + 40
      left = origin_x + 12
      right = left + popup_width + 12
      return popup_width, popup_height, left, right, top
    except Exception:
      return 800, 500, None, None, None

  def show_history(self):
    """Open a popup window showing the full timing history."""
    try:
      # Popup a simple HTML page bundled with the plugin that displays the history
      wFilePath = os.path.join(self.p_path, 'TimerPlus_history.htm')
      width, height, left, right, top = self._get_history_popup_layout()
      # Use a named popup so multiple calls reuse the same window
      try:
        olx.Popup('TimerPlus_history', wFilePath, b="tcr", t="TimerPlus History", w=width, h=height, x=left, y=top)
      except Exception:
        # Fallback to simple popup call without extra args
        olx.Popup('TimerPlus_history', wFilePath)
      if not self._history_modal_open:
        self._history_modal_open = True
        try:
          OV.ShowModal('TimerPlus_history')
        finally:
          self._history_modal_open = False
    except Exception as e:
      print("TimerPlus: could not open history popup: %s" % str(e))

  def show_search_results(self):
    """Open or refresh the separate search-results popup."""
    wFilePath = os.path.join(self.p_path, 'TimerPlus_search_results.htm')
    width, height, left, right, top = self._get_history_popup_layout()
    try:
      olx.Popup('TimerPlus_search_results', wFilePath, b="tcr", t="TimerPlus Search Results", w=width, h=height, x=right, y=top)
    except Exception:
      olx.Popup('TimerPlus_search_results', wFilePath)
    OV.ShowModal('TimerPlus_search_results')

  def edit_history(self):
    """Open the edit form popup for timing history."""
    try:
      wFilePath = os.path.join(self.p_path, 'TimerPlus_history_edit.htm')
      try:
        olx.Popup('TimerPlus_history_edit', wFilePath, b="tcr", t="Edit Timing History", w=600, h=360)
      except Exception:
        olx.Popup('TimerPlus_history_edit', wFilePath)
      OV.ShowModal('TimerPlus_history_edit')
    except Exception as e:
      print("TimerPlus: could not open edit history popup: %s" % str(e))

  def edit_history_for(self, mol_name):
    """Open the edit popup prefilled for a specific molecule name."""
    try:
      name = str(mol_name)
      # Set TIMER_MOL so the popup template can read it immediately via GetVar
      try:
        OV.SetVar('TIMER_MOL', name)
      except Exception:
        pass
      try:
        OV.SetVar('TIMER_ORIG_MOL', name)
      except Exception:
        pass
      # Also set time vars so inputs can be prefilled from template
      try:
        rec = self.molecule_timings.get(name, {})
        work_val = self._format_time(float(rec.get('total_work_time', 0.0) or 0.0))
        refine_val = self._format_time(float(rec.get('total_refine_time', 0.0) or 0.0))
        idle_val = self._format_time(float(rec.get('total_idle_time', 0.0) or 0.0))
        run_val = self._format_time(float(rec.get('total_run_time', 0.0) or 0.0))
        try:
          OV.SetVar('TIMER_WORK', work_val)
        except Exception:
          pass
        try:
          OV.SetVar('TIMER_REFINE', refine_val)
        except Exception:
          pass
        try:
          OV.SetVar('TIMER_IDLE', idle_val)
        except Exception:
          pass
        try:
          OV.SetVar('TIMER_RUN', run_val)
        except Exception:
          pass
      except Exception:
        pass
      wFilePath = os.path.join(self.p_path, 'TimerPlus_history_edit.htm')
      try:
        olx.Popup('TimerPlus_history_edit', wFilePath, b="tcr", t="Edit Timing History", w=600, h=360)
      except Exception:
        olx.Popup('TimerPlus_history_edit', wFilePath)
      # Ensure the fields are set after popup is created
      try:
        # Set the molecule display and the work time (try both qualified and bare names)
        try:
          olx.html.SetValue('TimerPlus_history_edit.EDIT_MOL', name)
        except Exception:
          pass
        try:
          olx.html.SetValue('EDIT_MOL', name)
        except Exception:
          pass
        try:
          self.set_edit_work(name)
        except Exception:
          pass
        # Also schedule a delayed prefill in case the popup wasn't ready yet
        try:
          safe = name.replace("\\", "\\\\").replace("'", "\\'")
          olx.Schedule(1, "spy.TimerPlus.set_edit_work('%s')" % safe)
        except Exception:
          pass
      except Exception:
        pass
      OV.ShowModal('TimerPlus_history_edit')
    except Exception as e:
      print("TimerPlus: could not open edit history popup: %s" % str(e))

  def _prefill_edit_popup(self, mol_name):
    """Helper scheduled to prefill popup controls if immediate SetValue failed."""
    try:
      name = str(mol_name)
      try:
        olx.html.SetValue('TimerPlus_history_edit.EDIT_MOL', name)
      except Exception:
        pass
      try:
        olx.html.SetValue('EDIT_MOL', name)
      except Exception:
        pass
      try:
        self.set_edit_work(name)
      except Exception:
        pass
      try:
        olx.html.Update()
      except Exception:
        pass
    except Exception:
      pass
  
  def _format_time(self, seconds):
    """Format seconds using the configured display resolution."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if seconds%60 >= 30:
      minutes += 1
      if minutes >= 60:
        hours += 1
        minutes = 0
    try:
      resolution = str(OV.GetParam('TimerPlus.time_resolution', 'second')).lower()
    except Exception:
      resolution = 'second'
    if resolution == 'minute':
      return "%02d:%02d" % (hours, minutes)
    secs = int(seconds % 60)
    return "%02d:%02d:%02d" % (hours, minutes, secs)

  def _parse_orca_total_runtime_seconds(self, log_path):
    """Extract TOTAL RUN TIME from an ORCA .wfnlog file and return seconds."""
    try:
      if not log_path or not os.path.exists(log_path):
        return None
      runtime_line = None
      with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
          if 'TOTAL RUN TIME:' in line:
            runtime_line = line.strip()
      if not runtime_line:
        return None

      m = re.search(
        r'TOTAL RUN TIME:\s*(\d+)\s+days\s+(\d+)\s+hours\s+(\d+)\s+minutes\s+(\d+)\s+seconds(?:\s+(\d+)\s+msec)?',
        runtime_line,
        re.IGNORECASE
      )
      if not m:
        return None

      days = int(m.group(1))
      hours = int(m.group(2))
      minutes = int(m.group(3))
      seconds = int(m.group(4))
      msec = int(m.group(5) or 0)
      return float(days * 86400 + hours * 3600 + minutes * 60 + seconds) + (float(msec) / 1000.0)
    except Exception:
      return None

  def _find_wfnlog_for_molecule(self, mol_name, data=None):
    """Resolve best .wfnlog path for a molecule entry."""
    try:
      rec = dict(self.molecule_timings.get(mol_name, {}) or {})
      if data:
        rec.update(data)
      base_name = str(rec.get('base_sNum') or mol_name or '').strip()
      # Remove optional user suffix: "name [User]" -> "name"
      if base_name.endswith(']') and ' [' in base_name:
        base_name = base_name.rsplit(' [', 1)[0].strip()

      candidate_dirs = []
      sdir = rec.get('sNumPath') or rec.get('filepath')
      if sdir and os.path.exists(sdir):
        candidate_dirs.append(sdir)
        candidate_dirs.append(os.path.join(sdir, 'olex2', 'Wfn_job'))
        candidate_dirs.append(os.path.join(sdir, 'Wfn_job'))

      # Fallback to Olex2 data samples area
      if base_name:
        candidate_dirs.append(os.path.join(instance_path, 'samples', base_name))
        candidate_dirs.append(os.path.join(instance_path, 'samples', base_name, 'olex2', 'Wfn_job'))

      checked = []
      for dpath in candidate_dirs:
        try:
          if not dpath or not os.path.exists(dpath):
            continue
          for stem in (base_name, mol_name):
            if not stem:
              continue
            p = os.path.join(dpath, '%s.wfnlog' % stem)
            checked.append(p)
            if os.path.exists(p):
              return p
        except Exception:
          continue

      # Last fallback: pick newest .wfnlog in candidate dirs
      newest = None
      newest_mtime = -1.0
      for dpath in candidate_dirs:
        try:
          if not dpath or not os.path.exists(dpath):
            continue
          for fn in os.listdir(dpath):
            if not fn.lower().endswith('.wfnlog'):
              continue
            p = os.path.join(dpath, fn)
            mt = os.path.getmtime(p)
            if mt > newest_mtime:
              newest_mtime = mt
              newest = p
        except Exception:
          continue
      return newest
    except Exception:
      return None

  def _get_wavefunction_time_str(self, mol_name, data=None):
    """Return formatted wave-function runtime for molecule from its own .wfnlog or N/A."""
    try:
      p = self._find_wfnlog_for_molecule(mol_name, data)
      sec = self._parse_orca_total_runtime_seconds(p)
      if sec is None:
        return 'N/A'
      return self._format_time(sec)
    except Exception:
      return 'N/A'

  

  def _parse_time_str(self, tstr):
    """Parse a time string (HH:MM:SS, MM:SS or seconds) into seconds (float)."""
    try:
      if not tstr:
        return 0.0
      s = str(tstr).strip()
      parts = s.split(':')
      parts = [p.strip() for p in parts if p.strip()!='']
      if len(parts) == 1:
        return float(parts[0])
      if len(parts) == 2:
        first = float(parts[0])
        second = float(parts[1])
        try:
          minute_resolution = str(OV.GetParam('TimerPlus.time_resolution', 'second')).lower() == 'minute'
        except Exception:
          minute_resolution = False
        if minute_resolution:
          return first * 3600.0 + second * 60.0
        return first * 60.0 + second
      if len(parts) >= 3:
        hours = float(parts[-3])
        minutes = float(parts[-2])
        secs = float(parts[-1])
        return hours * 3600.0 + minutes * 60.0 + secs
    except Exception:
      try:
        return float(re.sub(r'[^0-9.]', '', str(tstr)))
      except Exception:
        return 0.0

  def get_timing_edit_form(self):
    """Return HTML form for editing a molecule's work time."""
    try:
      # Only include saved molecules that are NOT the currently active molecule
      mols = [m for m in self.molecule_timings.keys() if m != self.current_molecule]
      if not mols:
        return "<div>No saved (non-active) molecules available to edit.</div>"
      # We'll return a small placeholder; the real controls are rendered in the edit page via html.Snippet
      return "<div><!-- edit form placeholder --></div>"
    except Exception as e:
      return "<div>Error generating edit form: %s</div>" % str(e)

  def update_history(self, mol_name, work_time_str, refine_time_str=None, idle_time_str=None, run_time_str=None, orig_mol_name=None):
    """Update stored timing values for `mol_name` and save to JSON.

    If `orig_mol_name` is provided and differs, the existing entry is treated as a rename.
    """
    try:
      if not mol_name:
        return "No molecule specified"
      mol = str(mol_name).strip()
      if not mol:
        return "No molecule specified"

      orig = None
      try:
        if orig_mol_name is not None:
          o = str(orig_mol_name).strip()
          if o:
            orig = o
      except Exception:
        orig = None

      # Rename behavior: if user changed molecule name in edit popup,
      # move the original record key instead of creating a duplicate row.
      if orig and orig != mol and orig in self.molecule_timings:
        moved = self.molecule_timings.pop(orig)
        if mol not in self.molecule_timings:
          self.molecule_timings[mol] = moved
        else:
          try:
            for k, v in moved.items():
              self.molecule_timings[mol].setdefault(k, v)
          except Exception:
            pass

      work_secs = self._parse_time_str(work_time_str)
      refine_secs = self._parse_time_str(refine_time_str) if refine_time_str is not None and str(refine_time_str).strip() != '' else None
      idle_secs = self._parse_time_str(idle_time_str) if idle_time_str is not None and str(idle_time_str).strip() != '' else None
      run_secs = self._parse_time_str(run_time_str) if run_time_str is not None and str(run_time_str).strip() != '' else None
      if mol not in self.molecule_timings:
        if refine_secs is None:
          refine_secs = 0.0
        if idle_secs is None:
          idle_secs = 0.0
        if run_secs is None:
          run_secs = work_secs + refine_secs + idle_secs
        self.molecule_timings[mol] = {
          'total_work_time': work_secs,
          'total_refine_time': refine_secs,
          'total_idle_time': idle_secs,
          'total_run_time': run_secs,
          'last_updated': datetime.now().isoformat()
        }
      else:
        rec = self.molecule_timings[mol]
        rec['total_work_time'] = work_secs
        if refine_secs is None:
          try:
            refine_secs = float(rec.get('total_refine_time', 0.0) or 0.0)
          except Exception:
            refine_secs = 0.0
        if idle_secs is None:
          try:
            idle_secs = float(rec.get('total_idle_time', 0.0) or 0.0)
          except Exception:
            idle_secs = 0.0
        rec['total_refine_time'] = refine_secs
        rec['total_idle_time'] = idle_secs
        if run_secs is None:
          run_secs = work_secs + refine_secs + idle_secs
        rec['total_run_time'] = run_secs
        rec['last_updated'] = datetime.now().isoformat()
      self.save_timing_data([mol])
      try:
        olx.html.Update()
      except Exception:
        pass
      return "Updated %s" % mol
    except Exception as e:
      return "Error updating history: %s" % str(e)

  def update_history_from_popup(self):
    """Read values from the edit popup controls (robust to naming) and update history."""
    try:
      # Try reading both qualified and bare control names for robustness
      mol_name = None
      for n in ('TimerPlus_history_edit.EDIT_MOL', 'EDIT_MOL'):
        try:
          v = olx.html.GetValue(n)
          if v:
            mol_name = v
            break
        except Exception:
          continue
      if not mol_name:
        try:
          mv = OV.GetVar('TIMER_MOL')
          if mv:
            mol_name = mv
        except Exception:
          pass
      work = None
      for n in ('TimerPlus_history_edit.EDIT_WORK', 'EDIT_WORK'):
        try:
          v = olx.html.GetValue(n)
          if v is not None:
            work = v
            break
        except Exception:
          continue
      if work is None or str(work).strip() == '':
        try:
          wv = OV.GetVar('TIMER_WORK')
          if wv is not None:
            work = wv
        except Exception:
          pass
      refine = None
      for n in ('TimerPlus_history_edit.EDIT_REFINE', 'EDIT_REFINE'):
        try:
          v = olx.html.GetValue(n)
          if v is not None:
            refine = v
            break
        except Exception:
          continue
      if refine is None or str(refine).strip() == '':
        try:
          rv = OV.GetVar('TIMER_REFINE')
          if rv is not None:
            refine = rv
        except Exception:
          pass

      idle = None
      for n in ('TimerPlus_history_edit.EDIT_IDLE', 'EDIT_IDLE'):
        try:
          v = olx.html.GetValue(n)
          if v is not None:
            idle = v
            break
        except Exception:
          continue
      if idle is None or str(idle).strip() == '':
        try:
          iv = OV.GetVar('TIMER_IDLE')
          if iv is not None:
            idle = iv
        except Exception:
          pass

      run = None
      for n in ('TimerPlus_history_edit.EDIT_RUN', 'EDIT_RUN'):
        try:
          v = olx.html.GetValue(n)
          if v is not None:
            run = v
            break
        except Exception:
          continue
      if run is None or str(run).strip() == '':
        try:
          tv = OV.GetVar('TIMER_RUN')
          if tv is not None:
            run = tv
        except Exception:
          pass
      if not mol_name:
        return 'No molecule selected'
      if work is None:
        return 'No work value'
      orig_mol = None
      try:
        ov = OV.GetVar('TIMER_ORIG_MOL')
        if ov:
          orig_mol = ov
      except Exception:
        pass
      if not orig_mol:
        try:
          ov = OV.GetVar('TIMER_MOL')
          if ov:
            orig_mol = ov
        except Exception:
          pass

      result = self.update_history(mol_name, work, refine, idle, run, orig_mol)
      try:
        OV.SetVar('TIMER_MOL', mol_name)
      except Exception:
        pass
      try:
        OV.SetVar('TIMER_ORIG_MOL', mol_name)
      except Exception:
        pass
      try:
        # Normalize the edit popup field to the saved value.
        self.set_edit_work(mol_name)
      except Exception:
        pass
      try:
        # Rebuild the history popup so the updated JSON-backed values are visible.
        self.show_history()
      except Exception:
        pass
      try:
        # Refresh the live timing values in the main UI as well.
        self.update_timer_vars(push_controls=True)
      except Exception:
        pass
      try:
        olx.html.Update()
      except Exception:
        pass
      return result
    except Exception as e:
      return 'Error reading popup values: %s' % str(e)

  def get_saved_molecules(self):
    """Return items string for saved (non-active) molecules suitable for gui/snippets input-combo-td."""
    try:
      mols = []
      for m in self.molecule_timings.keys():
        try:
          if m == self.current_molecule:
            continue
          if not m or str(m).strip() == '' or m == 'No structure loaded':
            continue
        except Exception:
          continue
        mols.append(m)
      # If there are no saved non-active molecules, fall back to including the current molecule
      if not mols and self.current_molecule and self.current_molecule != 'No structure loaded':
        mols = [self.current_molecule]
      items = ';'.join(["%s<-%s" % (m, m) for m in mols])
      return items
    except Exception:
      return ''

  def filter_history_display(self, search_term=""):
    """Filter history display by search term from the Olex2 input field.
    Reads TIMER_SEARCH variable which is populated by the native input field."""
    try:
      search_val = ""
      
      # Use parameter if provided
      if search_term:
        search_val = str(search_term).strip().lower()
      else:
        for control_name in ('TimerPlus_history.TIMER_SEARCH', 'TIMER_SEARCH'):
          try:
            val = olx.html.GetValue(control_name)
            if val is not None:
              search_val = str(val).strip().lower()
              break
          except Exception:
            pass
        if not search_val:
          try:
            val = OV.GetVar('TIMER_SEARCH')
            if val is not None:
              search_val = str(val).strip().lower()
          except Exception:
            pass
      
      self._history_search_term = search_val
      self.show_search_results()
    except Exception as e:
      pass

  def clear_history_filter(self):
    """Clear the history filter and show all structures."""
    try:
      self._history_search_term = ""
      for control_name in ('TimerPlus_history.TIMER_SEARCH', 'TIMER_SEARCH'):
        try:
          olx.html.SetValue(control_name, "")
        except Exception:
          pass
      try:
        OV.SetVar('TIMER_SEARCH', "")
      except Exception:
        pass
      self.show_search_results()
    except Exception:
      pass
  
  def get_filtered_history_popup(self):
    """Get formatted HTML table of timing history filtered by search term"""
    search_term = self._history_search_term.lower().strip()
    if not search_term:
      return "<tr><td colspan='7' style='text-align:center; color:#666;'>Enter a structure name above and click Search.</td></tr>"

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
    
    # Add all saved molecules (skip empty or placeholder keys)
    for mol_name, data in self.molecule_timings.items():
      try:
        if not mol_name or str(mol_name).strip() == '' or mol_name == "No structure loaded":
          continue
      except Exception:
        continue
      updated = data.get('last_updated', 'Unknown')
      try:
        s = str(updated).strip()
        if s and s not in ('Unknown', 'Active Now'):
          dt = None
          try:
            dt = datetime.fromisoformat(s)
          except Exception:
            for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
              try:
                dt = datetime.strptime(s, fmt)
                break
              except Exception:
                pass
          if dt is not None:
            updated = dt.strftime('%Y-%m-%d %H:%M:%S')
      except Exception:
        pass
      molecules_to_show[mol_name] = {
        'work': data.get('total_work_time', 0.0),
        'refine': data.get('total_refine_time', 0.0),
        'idle': data.get('total_idle_time', 0.0),
        'total': data.get('total_run_time', 0.0),
        'updated': updated,
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
      return "<tr><td colspan='7' style='text-align:center;'>No timing data available.<br/>Load a structure to start tracking.</td></tr>"
    
    html_rows = []
    # Sort by current first, then by last updated
    sorted_molecules = sorted(
      molecules_to_show.items(),
      key=lambda x: (not x[1]['is_current'], x[1]['updated'] if x[1]['updated'] != "Active Now" else "9999"),
      reverse=True
    )
    
    total_count = len(sorted_molecules)
    
    # Apply search filter if search term is set
    sorted_molecules = [
      (name, data) for name, data in sorted_molecules
      if search_term in name.lower()
    ]
    html_rows.append(
      "<tr style='background-color: #fff8dc; font-weight: bold;'>" +
      "<td colspan='7' style='text-align:center; color: #0066cc; padding: 8px;'>" +
      "Found %d of %d structures matching '%s'" % (len(sorted_molecules), total_count, search_term) +
      "</td></tr>"
    )
    if not sorted_molecules:
      html_rows.append(
        "<tr><td colspan='7' style='text-align:center;'>No structures found matching '%s'</td></tr>" % search_term
      )
    
    # Show ALL (no 5-item limit for popup)
    
    for mol_name, data in sorted_molecules:
      work_str = self._format_time(data['work'])
      refine_str = self._format_time(data['refine'])
      idle_str = self._format_time(data['idle'])
      wfn_str = self._get_wavefunction_time_str(mol_name, data)
      
      # Highlight current molecule
      bg_color = "#e8f4f8" if data['is_current'] else "#ffffff"
      
      # Escape single quotes for JS call
      safe_name = mol_name.replace("\\", "\\\\").replace("'", "\\'")
      name_link = self._molecule_name_link(mol_name)
      edit_link = "<a href=\"spy.TimerPlus.edit_history_for('%s')\">Edit</a>" % safe_name
      html_rows.append(
        "<tr style='background-color: %s;'>" % bg_color +
        "<td width='18%%' style='padding:6px;'>%s</td>" % name_link +
        "<td width='12%%' style='padding:6px; text-align:center;'>%s</td>" % work_str +
        "<td width='12%%' style='padding:6px; text-align:center;'>%s</td>" % refine_str +
        "<td width='12%%' style='padding:6px; text-align:center;'>%s</td>" % idle_str +
        "<td width='14%%' style='padding:6px; text-align:center;'>%s</td>" % wfn_str +
        "<td width='10%%' style='padding:6px; text-align:center;'>%s</td>" % data['updated'] +
        "<td width='22%%' style='padding:6px; text-align:center;'>%s</td>" % edit_link +
        "</tr>"
      )
    
    return "\n".join(html_rows)

  def get_search_results_html(self):
    """Generate a dedicated search results section when searching."""
    # If no search term, return empty (hide search results section)
    if not self._history_search_term:
      return ""
    
    search_term = self._history_search_term.lower()
    
    try:
      molecules_dict = self._load_timing_data()
      if not molecules_dict:
        return ""
      
      molecules_to_show = {}
      for mol_name, data in molecules_dict.items():
        updated = "Active Now" if data.get('last_updated') == "Active Now" else "Never"
        is_current = (mol_name == self._current_molecule_name)
        
        try:
          if not updated or updated == "Never":
            try:
              s = data.get('last_updated')
              if s:
                dt = datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
                updated = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
              for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
                try:
                  dt = datetime.strptime(s, fmt)
                  break
                except Exception:
                  pass
              if dt is not None:
                updated = dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
          pass
        
        molecules_to_show[mol_name] = {
          'work': data.get('total_work_time', 0.0),
          'refine': data.get('total_refine_time', 0.0),
          'idle': data.get('total_idle_time', 0.0),
          'total': data.get('total_run_time', 0.0),
          'wfn': data.get('wave_function_time', {}),
          'updated': updated,
          'is_current': is_current
        }
      
      # Sort by current first, then by last updated
      sorted_molecules = sorted(
        molecules_to_show.items(),
        key=lambda x: (not x[1]['is_current'], x[1]['updated'] if x[1]['updated'] != "Active Now" else "9999"),
        reverse=True
      )
      
      # FILTER: Only keep molecules matching search term
      filtered = [
        (name, data) for name, data in sorted_molecules 
        if search_term in name.lower()
      ]
      
      if not filtered:
        return (
          "<h3 style='color: #c0392b; margin-top: 15px;'>🔍 Search Results</h3>" +
          "<table style='border-collapse: collapse; width: 100%; background-color: #f9f9f9;'>" +
          "<tr><td colspan='7' style='text-align:center; padding: 15px; color: #c0392b;'>" +
          "No structures found matching '<b>%s</b>'</td></tr></table>" % search_term
        )
      
      # Build search results HTML
      html = (
        "<h3 style='color: #0066cc; margin-top: 15px;'>🔍 Search Results</h3>" +
        "<p style='font-size: 11px; color: #0066cc; margin: 5px 0;'>" +
        "Found <b>%d</b> structure(s) matching '<b>%s</b>'</p>" % (len(filtered), search_term) +
        "<table style='border-collapse: collapse; width: 100%; background-color: #f9f9f9;'>" +
        "<tr style='background-color: #34495e;'>" +
        "<th style='color: white; padding: 8px; text-align: left;'>Molecule Name</th>" +
        "<th style='color: white; padding: 8px; text-align: center;'>Work Time</th>" +
        "<th style='color: white; padding: 8px; text-align: center;'>Refine Time</th>" +
        "<th style='color: white; padding: 8px; text-align: center;'>Idle Time</th>" +
        "<th style='color: white; padding: 8px; text-align: center;'>Wave Function</th>" +
        "<th style='color: white; padding: 8px; text-align: center;'>Last Updated</th>" +
        "<th style='color: white; padding: 8px; text-align: center;'>Edit</th></tr>"
      )
      
      # Add filtered rows
      for mol_name, data in filtered:
        work_str = self._format_time(data['work'])
        refine_str = self._format_time(data['refine'])
        idle_str = self._format_time(data['idle'])
        wfn_str = self._get_wavefunction_time_str(mol_name, data)
        
        # Highlight current molecule
        bg_color = "#d5eef7" if data['is_current'] else "#ffffff"
        
        # Escape single quotes for JS call
        safe_name = mol_name.replace("\\", "\\\\").replace("'", "\\'")
        edit_link = "<a href=\"spy.TimerPlus.edit_history_for('%s')\">Edit</a>" % safe_name
        name_link = self._molecule_name_link(mol_name)
        html += (
          "<tr style='background-color: %s; border: 1px solid #bdc3c7;'>" % bg_color +
          "<td style='padding: 8px; width: 18%%;'>%s</td>" % name_link +
          "<td style='padding: 8px; text-align: center; width: 12%%;'>%s</td>" % work_str +
          "<td style='padding: 8px; text-align: center; width: 12%%;'>%s</td>" % refine_str +
          "<td style='padding: 8px; text-align: center; width: 12%%;'>%s</td>" % idle_str +
          "<td style='padding: 8px; text-align: center; width: 14%%;'>%s</td>" % wfn_str +
          "<td style='padding: 8px; text-align: center; width: 10%%;'>%s</td>" % data['updated'] +
          "<td style='padding: 8px; text-align: center; width: 22%%;'>%s</td></tr>" % edit_link
        )
      
      html += "</table><br>"
      return html
      
    except Exception as e:
      return ""

  def set_edit_work(self, mol_name):
    """Set all edit time input values for a given molecule name via olx.html.SetValue."""
    try:
      if not mol_name:
        return ''
      mol = str(mol_name)
      rec = self.molecule_timings.get(mol, {})
      work_val = self._format_time(float(rec.get('total_work_time', 0.0) or 0.0))
      refine_val = self._format_time(float(rec.get('total_refine_time', 0.0) or 0.0))
      idle_val = self._format_time(float(rec.get('total_idle_time', 0.0) or 0.0))
      run_val = self._format_time(float(rec.get('total_run_time', 0.0) or 0.0))
      try:
        try:
          olx.html.SetValue('TimerPlus_history_edit.EDIT_WORK', work_val)
        except Exception:
          pass
        try:
          olx.html.SetValue('EDIT_WORK', work_val)
        except Exception:
          pass
        try:
          olx.html.SetValue('TimerPlus_history_edit.EDIT_REFINE', refine_val)
        except Exception:
          pass
        try:
          olx.html.SetValue('EDIT_REFINE', refine_val)
        except Exception:
          pass
        try:
          olx.html.SetValue('TimerPlus_history_edit.EDIT_IDLE', idle_val)
        except Exception:
          pass
        try:
          olx.html.SetValue('EDIT_IDLE', idle_val)
        except Exception:
          pass
        try:
          olx.html.SetValue('TimerPlus_history_edit.EDIT_RUN', run_val)
        except Exception:
          pass
        try:
          olx.html.SetValue('EDIT_RUN', run_val)
        except Exception:
          pass
      except Exception:
        pass
    except Exception:
      pass
    return ''

  def get_work_time_for_dataset(self, dataset_name):
    """Return formatted HH:MM:SS work time for a named dataset from its _timer.json, or '' if unavailable."""
    try:
      strdir = olx.FilePath()
      if not strdir:
        return ''
      # olex2/ since 2026-10; older files sit in the structure folder itself
      fn = self._molecule_file(strdir, dataset_name)
      if not fn or not os.path.exists(fn):
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
mol = TimerPlus_instance.current_molecule
  
def get_sNum_and_path():
  """Return a stable, globally unique identifier for the current structure."""
  if olx.IsFileType("ires") == 'true':
    sNum = OV.ModelSrc()
  else:
    sNum = olx.xf.DataName(int(olx.xf.CurrentData()))
  # Combine with the directory to make it globally unique
  directory = os.path.normpath(OV.FilePath())
  return sNum, directory  
  
