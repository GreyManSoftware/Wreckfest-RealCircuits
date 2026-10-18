# tools

Local Windows helpers (phase 11). Not written yet.

- `Install-TrackMod.ps1`: download a generated mod zip and install it into
  `D:\SteamLibrary\steamapps\common\Wreckfest\mods\<slug>`; optionally stage it for the server
  (`mods=`, `el_add`, `Start-WreckfestServer.ps1` maps). Every server change shows a diff and asks first.
- Import a recorded `.tcat` back to the site.

Workshop publishing (option C) comes later and always needs an explicit approval click.
