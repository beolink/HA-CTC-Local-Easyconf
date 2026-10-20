# CTC Local Easyconf for Home Assistant

Local integration for CTC heat pumps. It talks straight to the unit on your own
network and never touches myUplink or any other cloud.

What you get: around fifty readings over Modbus TCP, the values only the
display has, delivered heat among them, and from those the heat pump's
coefficient of performance over a day, a week, a month, a year and its
lifetime; control of setpoints and modes through CTC's volatile registers,
never its stored settings; a **CTC** page in the sidebar that is built from
your installation and explains every value; and three actions for automations
and energy managers.

It runs on a CTC EcoZenith i255 driving an EcoAir 720M and on an EcoZenith i550
Pro, the two units it was built against, and on EcoZenith i360 units in
installations that send the anonymous statistics. Other CTC controllers with
the same BMS register map may well work, but none has been seen yet; one the
integration does not know is named after the family in its display's settings
file, as described under
[Setup](https://github.com/beolink/HA-CTC-Local-Easyconf#setup).

## Install

It needs Home Assistant 2026.5 or later, and Modbus TCP switched on at the
panel, with the display's web interface beside it for the values only the
display has; [Setup](https://github.com/beolink/HA-CTC-Local-Easyconf#setup)
says where on the panel.

**With HACS.** Until the integration is in HACS's default list, add it as a
custom repository: in HACS, the three dots at the top right, *Custom
repositories*, the address `https://github.com/beolink/HA-CTC-Local-Easyconf`
and the type *Integration*. Then search HACS for *CTC Local Easyconf*,
download it and restart Home Assistant. HACS offers every later release as an
update.

**By hand.** Download the latest release from
<https://github.com/beolink/HA-CTC-Local-Easyconf/releases>, copy its folder
`custom_components/ctc_ecozenith` into the `custom_components` folder of your
Home Assistant configuration, so that it ends up as
`config/custom_components/ctc_ecozenith`, and restart Home Assistant. Home
Assistant never offers an update for a copy put in place by hand; the
integration says in the repairs view when a newer release is out.

**Afterwards** it is found under *Settings, Devices and services, Add
integration*, by searching for *CTC Local Easyconf*, and the set-up first asks
how to find the heat pump; see
[Setup](https://github.com/beolink/HA-CTC-Local-Easyconf#setup). Once it is
set up, the heat pump is a device under *Settings, Devices and services, CTC
Local Easyconf*, its options are under *Configure* on the same page (see
[Options](https://github.com/beolink/HA-CTC-Local-Easyconf#options)), and the
**CTC** page is in the sidebar. Home Assistant installs the one library
the integration needs, pymodbus, by itself.

## What it reads

The integration uses two transports at once, because neither alone gives the
whole picture.

**Modbus TCP, port 502.** The documented BMS interface. Around fifty readings,
polled continuously, with no side effects: outdoor and room temperature, flow
and return, heat pump in and out, hot gas and suction gas, high and low
pressure, brine, compressor speed, immersion heater power, phase currents,
degree minutes, energy counters and the status enumerations. This is also the
only safe way to control the unit.

**The display's web interface, port 80.** CTC's own screen mirror, the same
thing myUplink proxies for its remote view. It carries values the Modbus map
simply does not have. The most useful is delivered heat in kilowatts alongside
supplied power, which gives a real coefficient of performance, plus the
expansion valve position, superheat, evaporation and condensation in bar, and
the inverter's own voltages and currents.

**The coefficient of performance over a day, a week, a month, a year and the
lifetime.** Only the display counts delivered heat, on its history page, called
stored operation data on older display software, so that page has to be among
the harvested ones. The week and the month are read against the daily samples
the integration keeps for the year, so they appear a week and a month after it
was set up, long before the first yearly figure; the display's own "/30 dagar"
rows are not used, since they stand at zero on both units they have been read
from. The energy consumed comes from the display's own counter where it has
one. The older software has none, and there it comes from Modbus register 62341
instead, which
holds the same number: 9166 kWh against the display's 9166.0 on an i255. It is
read at the moment the display is, so the two always form a pair. The sensors
exist from the start, whatever Modbus has said so far, and their attributes say
what is going on: a register that answers zero is a counter standing at zero,
which is how a controller that never writes it is told from one that is simply
new, and a register that has not answered at all is said to be missing, rather
than the sensors never appearing because one block was silent in the first poll.
Each sensor stands on its own span and never borrows another's: the lifetime
figure is not relabelled as a year, the yearly sensor says how many of its 365
days of samples it has and from when, the first year is carried by the
commissioning day alone and says when that day is unknown, and when the display
goes quiet the figures that rest on stored samples stay available and say since
when it has been quiet, while the daily figure goes with the display.

A figure is shown as soon as it rests on something: ten kilowatt hours of
consumption for the lifetime and yearly ones, three for a single day, which is
where the display's whole kilowatt hours stop being mostly rounding, and three
per day of the span for the week and the month, twenty-one and ninety in all.
What is not
shown is a quotient outside 0.5 to 10, because that is not a performance figure
but two counters that do not belong together. When a figure is missing the sensor
says why in its attributes, under *reason*: no sample old enough yet, too little
energy so far, a counter standing still although the unit has been running, or a
quotient that cannot be right. The sensor stays available and empty rather than
going unavailable, because an unavailable entity shows no attributes and the
reason is the point.

**The share of auxiliary heat, *Auxiliary heat share* (*Spetsvärmeandel*), over
the same spans but the first year.** How much of the electricity went to the immersion heater: its
counter, Modbus register 62191, against the sum of it and the compressor's
counter, the same consumed energy the coefficient of performance divides by,
in per cent. The integration keeps the immersion heater's counter in its own
samples since the share came in, so the lifetime share is there at once, the
day's after a day, and the week's, the month's and the rolling year's when a
sample with the counter is that old; samples from before have no immersion
heater and are passed over. The floors are the coefficient of performance's,
on the two energies together. A hundred per cent only when the compressor's
counter stood still while the immersion heater's moved, as on an i550 Pro whose
compressor counter has never counted, and an immersion heater that has used 1
of 9358 kWh reads 0.0, with the kilowatt hours beside it as *immersion_kwh* and
*compressor_kwh*. Without a figure it says why under *reason*: too little
energy, both counters standing still, a counter that went backwards, samples
from before the counter was kept, a register that has not answered, or a
compressor counter at zero although the compressor has run for a day (Modbus
62214), a controller that does not fill it, in which case no share is given
at all. It is never read off the display's "/30 dagar" rows and is not sent
with the statistics. A system coefficient of performance with the immersion
heater counted in is not offered: where the immersion heater is a rounding
error it is the same figure, and where the compressor counter stands still it
is 1.0 by definition.

**Starts, run length and the mean run.** The status register says what the heat
pump is doing every half minute, and the integration watches it from one round
to the next: when the compressor last started, how many times it has started
since midnight, and how long the last run lasted. A defrost in the middle of a
run is part of the run, not a stop and a start. The codes that say nothing about
the compressor, a communication error among them, hold the last known state, so
an error that comes and goes is not counted as starts. The first round after a
start of Home Assistant, or a reload of the entry, is the baseline, and nothing
is stored: the day's count starts over at midnight and whenever the entry is
reloaded, a restart of Home Assistant or saved options among them, and says
from when it counts. Where the history
page is harvested, the day's compressor minutes from Modbus divided by the
display's starts per day gives the mean run. The defrosts of an air to water
unit are counted the same way: how many since midnight, and when the last one
began, with its length and the outdoor temperature at the time as attributes,
which is where a healthy unit and an iced up one part ways. The defrost timer in
register 62137 is shown as the controller gives it; its unit is not confirmed.

**The transitions as events.** The same watch feeds an event entity, *Events*
(*Händelser* in a Swedish Home Assistant), with the event types
`kompressor_start`, `kompressor_stopp`, `avfrostning_start`,
`avfrostning_slut`, `larm`, `larm_borta`, `smartgrid_andrad` and
`systemstatus_andrad`. Each carries the code before and after, their labels, the
outdoor temperature and, for a stop or the end of a defrost, the length in
minutes. The logbook writes them out, and an automation triggers on the entity's
state instead of comparing states. The first round after a start of Home
Assistant raises none. The entity stays available when a Modbus round fails:
its state is the moment of the last event, which a lost line does not undo, and
the Modbus sensors themselves show that the line is down, so a state trigger
never sees the entity pass through unavailable and fire the old event again. On
Home Assistant 2026.9 or later the trigger `event.received` with an
`event_type` is the plainest way to listen. None of this goes into the
anonymous report.

**The alarm the panel shows, with its E-code.** Modbus says only that the heat
pump is off because of an alarm. The display prints the alarm itself out of its
own catalogue, `[E017] Givare solpaneler ut`, in the header icon at the top left
of a page and in the status field of a heat pump page, and the integration reads
those two places off the values it has already harvested: nothing is navigated
for it, nothing is polled faster, and the catalogue is never enumerated. The
sensor *Last alarm* (*Senaste larm*) holds the latest alarm as the panel
printed it, with the code, the text, when it began, when it went away and the
outdoor temperature at the start as attributes, and the binary *Alarm* (*Larm*)
carries the last ten episodes under *episodes*. A sensor alarm such as E017 leaves the heat pump running, so
the binary, which reads the Modbus status, can be off while the sensor shows an
alarm. Where the panel prints the alarm has been seen on one page of one model
so far, the history page of an i550 Pro, and is to be confirmed the next time a
unit alarms; the status field is read only on a row captioned *Status*, so a page
of past alarms cannot pass one of them off as current.

The binary sensor *Display alarm* (*Larm på displayen*) is on while the panel shows
an alarm, meaning a harvest has seen it there within the last harvest interval plus
five minutes (fifteen minutes where that is longer), or while the heat pump's own
alarm status is on. Its attributes give the code, the text, when it began, and
whether it is still confirmed with the time of the last sighting. The red alarm chip
on the overview follows it and names the code and the text. An episode closes only
when a harvest reads every page that can show an alarm and sees none; a harvest that
reads fewer pages leaves it open and unconfirmed, so an old alarm is not shown as live.
A label that a screen fixes on an icon, which the panel never picks by a variable,
is not an alarm.

## The catch with the display, and what the integration does about it

Only the page the panel is currently showing is kept up to date. Every other
screen returns a frozen snapshot from the last time it was rendered, and asking
for another page does not refresh it. Reading a different page means navigating
there, which moves the physical panel in your plant room.

So the display is treated as a supplement, not the base:

- Every page is ticked to begin with, and you choose which ones are worth the
  trip. The whole menu is kept, so a page can be switched on or off later under
  Configure without walking the panel again.
- A new version of the integration reads the menu again by itself, in the
  background: a newer parser understands rows and pages the older one passed
  over, and those are then read without anyone having to ask for it. A reading
  that lands while the panel is busy is tried again five minutes later, three
  times in all, and the log says so when they are spent. A page whose screen
  or caption did not answer during the reading is left out of it and the
  reading counts as owed, since a row without its caption would come back
  under another name; the stored menu stands until a reading is whole.
- A display row's entity is known by where the row stands on the page, the
  page, the screen and the first value it reads, not by its name. A newer
  parser that reads a row's name better renames the entity and keeps it, its
  history and whatever you set on it. Up to 0.18.0 the key was the name, so the
  first reading of the menu after updating from there carries every row's
  entity over to the key of its place: only the entity's unique id changes,
  never its entity id, so dashboards and automations keep working, and what
  the integration remembers about each row moves with it. The log says on info
  which entities were carried over. The carrying over goes one way. Put 0.18.0
  back after it, and 0.18.0 reads the menu again with its own parser, removes
  the carried over entities from the registry and creates them anew: most come
  back with the same entity id and their history, but what you set on them, a
  name, an area, hidden or switched off, is lost, and so is an entity id you
  changed yourself. Take a backup of Home Assistant before you update from
  0.18.0, and go back by restoring it rather than by installing 0.18.0 again:
  the backup puts the integration back together with the entity registry, the
  entries and the integration's stored files as they were.
- They are polled on a slow interval, thirty minutes by default.
- A restart of Home Assistant costs the panel nothing. The last harvest is
  kept, values and the moment each was read, so the sensors come up with it,
  and the next harvest is made when it would have been made anyway, one
  interval after the last one. Only an installation that has never harvested
  walks a few seconds after start. The serial number and the firmware versions
  are read in the background too, never during set-up.
- The panel is put back where it was afterwards.
- A walk asks the display for as little as it can. The operation data tile on
  the home screen is looked up once, between two pages the panel steps back to
  the operation data menu and replays the route from there instead of going
  home for each, and the serial number and firmware versions are read from
  the two screens they live on once those are known, rather than from every
  screen in the map. The log says on debug what each walk cost in requests
  and presses.
- If the panel is not where the integration left it, somebody is standing at it,
  and that cycle is skipped, with one page selected as much as with seven. Two
  cycles in a row are given way; the third is harvested anyway and the panel is
  put back on the page it was found on, so a panel left on another page does
  not stop the harvest for good. The first skip and the resumption are each one
  line in the log.
- The device's diagnostic sensor *Last display harvest* (*Senaste
  displayskörd*) says when the display was last read, and in its attributes
  how many harvests in a row were skipped
  or failed, when the next attempt is, the reason for the last empty one, and
  which pages were read and missed. The CTC page names a pump that Home
  Assistant is retrying, with the reason, instead of saying that nothing runs.
- Reading the menu again from Configure takes the same turn at the panel as
  the harvest and the walk to the system information page, so two of them can
  never walk it at once. Is the panel busy when you ask, the dialog says so and
  nothing is changed; ask again in a moment. The rest of the form is kept and
  saved with the pages you tick, and the three background tries start over, so
  a menu the background had given up on is read again after the save. A
  reading that misses writes neither the menu nor the version.
- A page the reading of the menu does not reach can be searched for once,
  under Configure, *Search for the way to a page*. The reading tries at most 26
  controls, two levels deep, and on an i550 Pro whose operation data root is a
  schematic with many places to press that is spent before *Kompressor VP1*,
  the page with the delivered heat and the power input. Give the page's
  number, from the display's screen map at `http://<display>/sm/all` (the
  number before each colon), or walk the panel to the page by hand and the
  form offers the page it shows. The search starts at the operation data
  root, tries at most 40 controls at most three presses deep, stops at the
  page, records the way there, reads the page as the reading of the menu does
  and puts the panel back where it was, or on the home screen where it does
  not know the way back. It presses only what the reading would press, never
  a control whose caption names Avancerat, Service, Define or the language
  selection, and nothing on a page whose heading names one of them or whose
  own back button leads out of the operation data menu, but that button. It
  takes the same turn at the panel as every other walk, and runs only when you
  submit the form, never by itself, so stand at the panel while it runs. What
  it found is shown first; submitting adds the page to the menu, ticked, with
  the way the harvest then takes to it, and the reading of the menu after an
  update keeps the page while it is ticked. A page it does not find within its
  limits is said on the form and nothing is saved. An i550 Pro draws the way
  to *Kompressor VP1* only in some operating states, so a search that misses
  it may find it later.

There is no second session to escape this with. The `/click2/` and `/scroll2/`
endpoints exist in the display's own JavaScript but the firmware answers 400 to
every form of them, on both models tested.

## Setup

1. On the panel, go to Installer (Advanced on some displays, Avancerat on a
   Swedish one), Define, Remote control and set **Ethernet** to
   **Modbus TCP**. The port row only appears once that is done. Note that this
   is reported to be mutually exclusive with the cloud connection. For the
   display's values, its web interface has to be on as well: Installer, Define,
   Communication, **Web** = Yes. What the two open on your network, and how to
   fence them in, is under [Network and exposure](#network-and-exposure).
2. Add the integration. It first asks how to find the unit, and nothing goes on
   the network before you have chosen. **Search the network** asks every
   address on Home Assistant's own networks for `/settings/name` on port 80,
   which is how a CTC display tells itself apart, and changes nothing anywhere.
   Only the networks Home Assistant itself is attached to are searched, never
   the operating system's guess, which inside a container is the docker
   bridge, and of those only the private ones: a network of public addresses,
   Home Assistant on a rented server for instance, is never searched, since
   that would be a port scan of strangers. A network of more than 1024
   addresses, a /16 for instance, is searched as the /24 around Home
   Assistant's own address on it. Addresses that already have an entry are
   left out of the list, and a search that found nothing else says so rather
   than that it found nothing. **Enter an address** skips the search; use it if
   yours is on another subnet, or the search finds nothing.
3. Tick the display pages you want harvested. The menu is read from the unit
   itself, so the list matches your model and your installed options. The
   titles are the display's own, in Swedish, the language the integration reads
   the display in, whatever the panel is set to, and so are the names of the
   display rows' entities; see [Language](#language). Everything is ticked to
   begin with; switch off what you do not want. For the coefficient of
   performance, keep the page with the stored or historical operation data.

**When Modbus does not answer.** If the display answers and Modbus does not, a
step of its own says where on the panel Modbus TCP is turned on, and takes the
address again. The controller takes one Modbus client at a time, and when
another one holds the place it accepts the connection and drops it at the first
request, which the Modbus library reports like a pump that is switched off. So
when Modbus fails, and only then, with the integration's own connection closed
and the controller's settle time waited out, the integration opens the port once
more with a plain socket and asks for one register. A connection dropped like
that gets a step of its own, **Modbus is taken**, that names the usual culprits:
a `modbus:` block in `configuration.yaml` pointing at the same unit, a test
tool, or a session that was never let go. A reset that comes before the
request, logged as `Connect call failed [Errno 104]`, counts as a taken place
too. Something that answers on the port but not with the register, a Modbus
error or a web server on the port typed for Modbus, sends the address form back
with the port and the Modbus address to check. The same probe runs when an entry
cannot read a register at start-up: the entry's retry reason names the cause,
and a taken place is also said in the repairs view until the first reading that
works. A verdict is reused for ten minutes, so Home Assistant's retries do not
knock twice as often.

**A display without its web interface.** A typed address whose web interface
does not answer at all, with Web switched off, an older display, or a firewall
that lets 502 through and nothing else, is added on Modbus alone if Modbus
answers. Such an entry has no display pages, no coefficient of performance and
no serial number; it reads no menu and walks no panel, and the repairs view says
nothing about pages or the serial number. Turn the web interface on and tick
*Read the display's menu again* under Configure, and it becomes an entry with a
display. A unit found by the search or by DHCP always has its display.

The display names its family by a settings file, `settings_ezi2xx.bin` on an
i255, and that decides the model the device is given. A file the integration
does not know yet gives *EcoZenith* followed by the family part of its name,
EcoZenith (ezi4xx) for instance, so the device is called CTC EcoZenith
(ezi4xx).

Four things are switched on from the start, and the set-up says so on its way:
control of the heat pump, the walk to the System information page described
below, the anonymous statistics and the daily check for a new release. All four
are changed under Configure.

Give the display a fixed address, a DHCP reservation in your router, so that
it keeps the address the integration was set up with. Should it get another
one all the same, nothing needs doing: the device and its entities are known
by a key of their own that stays with them, not by the address, and when
Home Assistant sees the display ask DHCP for its new address it recognises the
unit by the MAC address on the display's system information page and moves
the entry there, history, entity ids and settings included. The MAC is read
together with the serial number, so this works once the serial number shows
on the device page, and it needs Home Assistant to see the display ask for its
address: on the same network it does, and on another one a router integration
in Home Assistant that tracks the network's devices can tell it. Where neither
is the case, remove the integration and add it again at the new address. An
entry set up with a host name rather than an address, the display's own CTC
name or one your router's DNS gives it, is left on that name, which follows
the display by itself. A move connects at the new address only once the
controller's settle time after the old session has passed, as after any
reload, since both sessions are to the same pump. A second heat pump that later
gets the address the first one was set up with is added like any other, with a
device and entities of its own. No entry is ever moved to an address another
entry already has, since the controller takes one Modbus client at a time: if
the heat pump was added a second time at its new address while the old entry
stood waiting, the old one stays where it was, the log says so on warning, and
the one that does not answer is yours to delete. The ports and the Modbus
address are changed the same way, by removing the integration and adding it
again: on a CTC they are 80 for the web interface, 502 for Modbus and address
1, and have no reason to change, so there is no separate dialog for them.

The serial number and the display's own software version are only written into
the System information page while that page is shown on the panel. The
integration walks there once to read them, and puts the panel back. It presses
nothing but Advanced, Display and System information, matched on the English
label so it works whatever language the panel is set to, checks where the panel
went after every press, and gives up at the first surprise. The service menu,
which holds a function test, a compressor quick start and reinstallation, is
never opened. Switch it off under Configure if you would rather open that page
yourself. What the walk finds goes straight into the device and its sensors;
nothing is reloaded for it. The screens the identity lives on, the system
information screen and the heat pump's own, are read again at every start,
which moves nothing, so a firmware update of the display shows on the device
page without anyone asking; a field the display has not written since it
restarted keeps the value it had.

When something is left for you to do, it is said in Home Assistant's repairs
view rather than only in the log: the page the coefficient of performance needs
is not harvested, the serial number has not been read yet, or a newer version
has been released. Home Assistant only offers updates for what HACS installed,
so a copy put into `custom_components` by hand is never offered one; the release
check asks GitHub once a day and can be switched off under Configure.

Everything the unit offers is created and switched on: every register, every
row of every harvested display page, and the controls. Nothing is written to the
heat pump by their existence alone, since a control mirrors the unit until
somebody sets it, and the page leaves out what this installation has only ever
reported as zero. Switch off what you do not want, per entity on the device page
or, for control as a whole, under the integration's options. An installation
from before 0.14.0, when part of this was created switched off, has those
entities switched on at the first start of a newer version, with one line in
the log, and Home Assistant reloads the entry once about thirty seconds later;
the panel is left where it is until then. A display row that only gets its
entity later, once it first reads as a number, is switched on at the start
after that. Whatever you switched off yourself stays off, and so does
everything while the entry's system option **Enable newly added entities** is
off: Home Assistant then registers every new entity switched off on your
behalf, and the integration leaves that choice alone.

## Options

Under *Settings, Devices and services, CTC Local Easyconf, Configure*. Saving
the form reloads the entry, unless *Report all values to the developer* is all
that changed: that one is applied without a reload.

| Option | From the start | What it does |
|---|---|---|
| Pages to read | every page of the menu | Which display pages are harvested. The figure after each page is how many values it holds. Shown once the menu has been read. |
| Modbus interval | 30 s, from 10 to 600 | How often the Modbus registers are read. |
| Display interval | 1800 s, from 300 to 21600 | How often the ticked pages are harvested; every harvest walks the physical panel, which is why it is slow. |
| Put the panel back afterwards | on | Returns the panel to the page it was found on after a harvest. |
| Allow controlling the heat pump | on | Creates the controls, which write only to the volatile 1000 block; see [Control](https://github.com/beolink/HA-CTC-Local-Easyconf#control). |
| Let the integration open System information on the panel | on | The one walk to the page with the serial number and the display's software version; see [Setup](https://github.com/beolink/HA-CTC-Local-Easyconf#setup). |
| Tell me when a new version is released | on | Asks GitHub once a day and says so in the repairs view. |
| Send anonymous usage statistics | on | One report a day; see [Anonymous statistics](https://github.com/beolink/HA-CTC-Local-Easyconf#anonymous-statistics). Switching it off stops the reporting; what was sent stays. |
| Report all values to the developer | off | Sends what *Download diagnostics* holds, and the integration's own warnings and errors, every 30 minutes until it is switched off; see [Debug report](https://github.com/beolink/HA-CTC-Local-Easyconf#debug-report). The same setting as the switch on the CTC page, and switching it reloads nothing. |
| Read the display's menu again | off | Walks the menu now and offers the pages again on the next form; see [The catch with the display](https://github.com/beolink/HA-CTC-Local-Easyconf#the-catch-with-the-display-and-what-the-integration-does-about-it). |
| Search for the way to a page | off | For a page the reading of the menu does not reach: asks for its number on the next form and searches for the way there once, with somebody at the panel. Offered where the entry has a display; see [The catch with the display](https://github.com/beolink/HA-CTC-Local-Easyconf#the-catch-with-the-display-and-what-the-integration-does-about-it). |

The address, the ports and the Modbus address are not options: see the end of
[Setup](https://github.com/beolink/HA-CTC-Local-Easyconf#setup) for how a new
address is followed and how the ports are changed.

## Network and exposure

The integration needs two doors into the heat pump, and neither of them has a
lock.

**The display's web interface, port 80.** CTC's screen mirror, switched on at
the panel under Installer (Avancerat), Define (Definiera), Communication
(Kommunikation) by setting **Web** to **Yes**; the names can differ a little
between models and display software. It answers anyone who can reach the
display, with no login: every screen can be read, and every place on the panel
can be pressed exactly as if somebody stood in front of it. That includes the
installer menus, where Service holds a function test, a compressor quick start,
a reinstallation and a firmware update, and Define holds the settings that
decide how the plant runs. The device's link on the device page opens this same
page, for anyone whose browser can reach the display, not only for people with
access to Home Assistant.

**Modbus TCP, port 502.** Switched on with **Ethernet** set to **Modbus TCP**,
as under Setup. It has no authentication either: anyone who reaches the port can
read every register and write the control registers, and nothing on the unit
stops a client from writing the stored settings in the 61500 block, whose
memory tolerates a limited number of writes. This integration refuses those
addresses in its own code; another client on the network is under no such
rule. And since the controller serves one Modbus client at a time, anything
else that connects disturbs this integration's session as well.

The integration cannot add a login to either: both are the unit's own servers,
and it only talks to them. What it can do is ask you to keep them out of reach:

- Put the display on a network that only Home Assistant can reach: a VLAN of
  its own for the heat pump, or a firewall rule that lets Home Assistant's
  address, and nothing else, reach ports 80 and 502 on the display. Give the
  display a fixed address, a DHCP reservation in the router, so the rule keeps
  pointing at it. Either way, the device's link on the device page then opens
  the panel only on a computer the rule also lets through to port 80, or one
  on the heat pump's VLAN; from any other, it does not answer.
- **Never open port 80 or 502 to the internet.** No port forwarding, no DMZ,
  and no UPnP opening anything for the display. Anyone who found it there could
  steer the heat pump and walk its installer menus.
- Home Assistant's own remote access, Home Assistant Cloud or a reverse proxy,
  gives you the CTC page, the values and the controls from away, but not the
  display's own page: the integration does not pass that page through Home
  Assistant, and the device's link sends your browser straight to the
  display's address on your network. To reach the panel's page from away, go
  through a VPN into your network, never straight to the display.

## The CTC page

Setting the integration up adds a **CTC** entry to the sidebar on its own.
There is nothing to configure and no button to press: the page is built the
moment it is opened, from the entities the integration has at that moment, so
it is never out of date. Each heat pump gets four tabs.

- **Overview.** What the pump is doing right now: the controller's status and
  the heat pump's own as a line of chips, the handful of controls worth reaching
  for, the circuit over the last day and, under it, what the pump did when, as
  timeline bands of the two statuses and the immersion heater, and the key
  figures as large numbers. The status chips that are booleans, an alarm, a
  block, a defrost, a running compressor or heater, SmartGrid, are on the line
  only while they are on, in the theme's colour for what they mean: an alarm is
  a red chip, and a quiet pump shows no row of "Off". While Home Assistant
  holds any control register, a small section says how many, with **Release
  all control** beside the count.
- **Controls.** Everything writable, as the thing it is, a mode picked from a
  list, a setpoint slid or typed, grouped by what it does to the house rather
  than by entity domain: heating, hot water, operation and power. Letting go of
  every override at once is the last row under operation and power, and its
  explanation says why it exists. Last of all, under **Troubleshooting**
  (*Felsökning*), is a tick box, **Report all values to the developer**
  (*Rapportera alla värden till utvecklaren*), there whether control is on or
  off; see [Debug report](#debug-report).
- **Performance.** How the pump has run: the flow against its setpoint and the
  compressor over a day, energy, the compressor's running time and the
  coefficient of performance per day over a month, a bar per day, and then
  every reading in its own section, temperatures, hot water, energy, the
  refrigerant circuit, the settings stored in the heat pump and what the unit
  says about itself.
- **All values.** The full list with a search field over it, every value the
  installation offers, the display's own pages among them under the names the
  panel prints. Nothing is left out here whatever it reads, which makes it the
  tab that answers whether a value exists at all. A row with nothing to show,
  unavailable or unknown, waits behind the box *Show empty ones too* until it
  is ticked, and a search finds it there all the same; the count beside the box
  says how many rows are shown, how many have a value and how many there are.
  A display row's source line names its screen and its place on the page, so
  two rows by one name can be told apart. The search and the box are kept for
  as long as the browser tab is open.

A control Home Assistant holds right now is marked **Overridden** (*Styrd* on a
Swedish page) after its name, among the controls and in All values, and the
mark's hover text says what holding means: Home Assistant writes the value
again every minute, and the pump forgets it about five minutes after the last
write. Where the integration gives the pump's own value under the override, it
stands under the row as *pump's own* (*pumpen själv*). A held number has a
**Release** button that lets go of that one alone, through the integration's
release service, without writing anything; a mode is let go of with its own
option, *Släpp styrningen*, which keeps that Swedish name in every language.
At rest a mode list among the controls stands on *Släpp styrningen* with the
pump's own mode under it, so any mode, the one the pump runs included, can be
picked and held. Searching All values for *overridden* (*styrd*) lists
everything held.

The coefficient of performance says under its figure what it rests on, the
last day, the last seven or thirty days, a rolling year, the first year or the
whole life, and while it has no figure it says why instead: no sample old
enough yet, too little energy so far, a counter standing still. The share of
auxiliary heat does the same, and the page says both lines in its own
language, though the attributes behind them stay Swedish (see
[Language](#language)). Each stays on the page as long as its sensor does.

The cards are the integration's own rather than Home Assistant's tiles, because
a heat pump has a hundred values and they have to fit on a screen: chips for the
status, a small name over a large number for the key figures, one row with the
control itself for anything writable, and everywhere else a dense list, name on
the left and value on the right, in as many columns as the screen has room for.

The display's own web interface is one click away from the device page, under
*Settings, Devices and services*: the device's link opens the panel's own page
in a new tab, on a computer that reaches the display itself, at home and past
any firewall rule that keeps the display for Home Assistant (see
[Network and exposure](#network-and-exposure)); through Home Assistant's remote
access it does not open. It is not on the CTC page, because a browser will not
show an http page inside a Home Assistant reached over https, and because the
panel answers it from the same small web server the integration harvests from.

**Every value is explained.** Beside each one is a blue ⓘ, and it writes the
explanation out underneath: what the value is, where it comes from, a Modbus
register and whether it is a stored setting or a volatile control register, or
the display page, the screen and the row's place on it, and how often it is
read, and a link to Home Assistant's own
dialog for the entity. In the list a tap anywhere on the row does the same; a
row without an explanation, in a list written by hand or started from the card
picker, opens Home Assistant's dialog for the entity instead. The
ⓘ is the one way in for the keyboard and a screen reader too: a button of its
own, one tab stop per value, which says whether its explanation is open and
which text it opens, and the name carries no hover text that would read the
explanation a second time. The explanations come
from CTC's BMS manual and CTC's own description of the operation data rows, and
say so where a register's meaning is not confirmed on a running unit. They are
written in Swedish and in English, and the page shows them in Home Assistant's
language; see [Language](#language).

**What is on the page is decided by your installation.** Heat pumps, indoor
units, software revisions and settings differ, and the controller answers for
hardware that is not fitted with a clean zero: a brine pump on an air to water
heat pump, current sensors that were never installed, the refrigerant circuit of
a unit whose compressor has never run. So a reading that your installation has
only ever reported as zero is left off, and it turns up the first time it has a
value. The integration remembers what it has seen, and the first time it runs it
looks through the past year of Home Assistant's own statistics, so a compressor
that is merely at rest right now is not mistaken for one that is missing. Status,
controls and the settings stored in the heat pump are shown whatever they read,
and so is everything on All values, which is the point of that tab.

A reading with nothing to show, such as a sensor CTC reports as not fitted, a
display page not reached yet or a yearly figure without a year of history, is
hidden until it has a value, and a section goes when all of its readings have.
Status and control are always shown. Entities you disable or hide are left off,
and a name you give an entity is the name on the page. So are values from a
display page that is no longer harvested, which stay in the entity registry as
unavailable until you delete them.

While the page is open it follows along: a reload of the integration, or an
entity enabled, disabled or renamed, makes it fetch the new layout by itself.
The page is read only, since the next opening would build it again anyway.

The explanations need the integration's card script, which is added as a
Lovelace resource on its own. A browser that already has Home Assistant open
loads resources once per page load, so **reload the page once after installing
or updating** the integration. Where resources are managed in
`configuration.yaml` the script is injected into the frontend instead; should
the page say there that a custom element does not exist, `ctc-ecozenith-rows`
or another of the page's cards, add `/ctc_ecozenith/ctc-ecozenith-card.js` as
a `module` resource yourself.

Home Assistant has no public call for adding a dashboard from an integration, so
the page is registered the way Lovelace registers a dashboard from
`configuration.yaml`, and checked before use. If a later Home Assistant moves
those parts, the integration logs it and runs on without the page. An address
that is already taken, `/ctc-ecozenith`, is left alone.

### The section card

A section of the page can go on a dashboard of your own, explanations and all,
without writing a single value or text by hand. The card picker lists it as
**CTC: section of the page** (*CTC: sektion av sidan* when Home Assistant is in
Swedish), and its editor asks for two things, the heat pump and the section. In
YAML:

```yaml
type: custom:ctc-ecozenith-section
device_id: 0123456789abcdef0123456789abcdef   # the heat pump's device id
section: energy
```

The editor fills the device id in for you; by hand, it is the last part of the
address of the heat pump's page under *Settings, Devices and services*. The
sections are those of the page, by tab:

| Tab | `section` |
|---|---|
| Overview | `status`, `overrides`, `quick_controls`, `key_readings` |
| Controls | `heating_controls`, `hot_water_controls`, `power_controls`, `troubleshooting` |
| Performance | `temperatures`, `hot_water`, `energy`, `compressor`, `cycles`, `settings`, `unit`, `other` |
| All values | `values` |

The card asks the integration for its section when it is shown, and again
whenever an open page would fetch its layout again, so it shows what the page
shows there: the same cards with the same explanations, the readings your
installation has only ever reported as zero left out the same way, and nothing
the page does not show. The texts are never copied into your dashboard, so they
follow the integration when it is updated. A section the page leaves out for
your pump, such as the controls while control is switched off, leaves the card
empty, and in the editor it says so. The `overrides` section, what Home
Assistant holds in the pump, is drawn only while it holds anything, as on the
page. Anyone signed in can see the card, as
anyone signed in can open the page. The graphs are Home Assistant's own cards
and are not offered; put a history or statistics graph beside the section card.

The page's other four cards are in the picker as well, as **CTC: chips**,
**CTC: key figures**, **CTC: controls** and **CTC: list of values**. They take
their entities as a list, under `items` or, for the list, `rows`, and the picker
starts them with a few of the integration's own. The page's explanations come
only with the section card.

## Language

The integration follows the language Home Assistant is set to, under
*Settings, System, General*: Swedish when that is Swedish, English for every
other language. That covers the CTC page, its headings, its words for states
and every explanation and source line behind the ⓘ, and the names of the
integration's own entities, which come from its translations. After a change
of language the page changes the next time it is opened, and the entity names
at the next restart of Home Assistant or reload of the entry; the integration
does not reload itself for it, since a reload reads the display again and moves
its panel. The system language under *Settings, System, General* sets the page
and the names, while the setup and options forms, the repairs view and the
actions follow the language of each user's own profile.

Some things stay as they are whatever the language:

- **The display rows are named by the panel.** Each display row's entity is
  called by the page title and the row the panel prints, and the integration
  reads the panel's menu and captions in Swedish, whatever language the panel
  itself is set to; that setting is unchanged. So on an English Home Assistant
  the display's rows keep Swedish names such as *Driftinfo, Historik: Avgiven
  värme totalt*, explained in English.
- **States, modes and options are the controller's Swedish words.** The status
  sensors read *Värmepump övre* or *Till varmvatten*, a mode list offers
  *Ekonomi*, *Normal* and *Komfort*, and every control list keeps *Släpp
  styrningen* for letting go, in every language: automations, energy managers
  and the history are built on those exact values. The explanations say what
  each of them means.
- **Entity ids never change.** Home Assistant makes an entity's id once, from
  its name in the language of the day it is first added, and keeps it. An
  installation from before the translations keeps every id it has, such as
  `sensor.ctc_ecozenith_i255_utetemperatur`, and only the name shown changes,
  to *Outdoor temperature* on an English Home Assistant. A new installation
  gets ids from the names in its language, `sensor.ctc_ecozenith_i255_outdoor_temperature`
  in English and the Swedish ids as before in Swedish. A name you gave an
  entity yourself is kept.
- **Attribute values in text stay Swedish**, such as the reason a coefficient
  of performance gives for an empty figure, so that automations read the same
  words in every language; the attribute names have English twins, see
  [Attribute names](#attribute-names). The CTC page shows the line under the
  figure, what it rests on or why it has none, in the page's language all the
  same.

## In the Energy dashboard

Two of the Modbus readings belong in Home Assistant's Energy dashboard, as
individual devices: **Compressor energy**, in Swedish **Kompressorenergi**,
register 62341, the electricity supplied to the heat pump, and **Immersion
heater energy**, in Swedish **Elpatron energi**, register 62191, the immersion
heater's. Both are the controller's lifetime counters in kWh, with
the state class `total_increasing`, so the dashboard takes them as they are.
Where the display's history page has *Tillförd energi totalt* and *Energi el
total*, those are the same two counters (9166 kWh against 9166.0 on an i255);
add one pair, never both, and by default the Modbus pair is read every half
minute rather than every half hour. Not every controller keeps them: an i550 Pro has
been seen with 62341 standing at zero.

The display's history page also counts the heat the pump has delivered, in a
row called *Avgiven värme totalt*, or *Avgiven energi* on older display
software, which by CTC's manual is what the i360 has. Whatever the row is
called, it is a counter in kWh too, and Home Assistant offers it in the same
list, but it is the heat the pump has given off, not electricity it has used,
several times as much on any day the pump works well. Added as a device it
books the pump's whole output as consumption. It is what the coefficient of
performance divides by the consumption, which the integration does for you.

## Control

Control writes only to CTC's volatile 1000 block: maximum compressor speed,
immersion heater limits, room and hot water setpoints, hot water and price mode,
zone mode. Those registers are not stored in EEPROM, so they can be written as
often as needed, and the controller forgets them roughly five minutes after the
last write. That expiry is the safety net. If Home Assistant stops, the heat
pump quietly returns to its own settings.

A mode is released by choosing *Släpp styrningen*, the option's name in every
language. A setpoint has no such position in a list; while one is held, its row
on the CTC page has a **Release** button (*Släpp*) of its own, and the action
`ctc_ecozenith.release` lets go of one control, or a few, and leaves the rest
held; see [Actions](#actions). The button **Release all control** (*Släpp all
styrning* in a Swedish Home Assistant) stops every override at once. None of
them writes
anything: Home Assistant stops writing, and the controller goes back to its own
settings within about five minutes.

What Home Assistant shows is what the controller holds. An override counts as
in force only from a write that actually reached the unit, each control entity
carries the attributes *last_written* and *valid_until*, and when no write has
reached the unit for five minutes the override is released on this side too,
with one line in the log, since the controller has forgotten it by then. A
released override is not taken up again by itself when the unit answers again:
the entity then shows the unit's own setting with *control_active: false*, and
whoever set the override, a person or an energy manager, sets it anew. An
energy manager that reads the entity should treat that combination as a lost
command rather than as somebody's manual change. The release comes at
*valid_until* itself, not at the keepalive's next minute.

The controls stay available when a Modbus round fails, so a value or a mode
set from an action in that moment is written rather than skipped, and a held
control keeps its attributes. A round that failed only takes the pump's own
value away: an idle control that shows it then shows *unknown* until the next
round answers, which the CTC page writes as *not read* (*ej läst* on a Swedish
page) rather than *not set* (*ej satt*).

Every control says whether it is held, in *control_active*, and a control
with a stored setting beside it also carries the pump's own value, in
*pump_value*, while Home Assistant holds a value of its own as much as while
it does not: the room setpoint, the compressor's top speed, the two immersion
heater limits and the hot water mode. Extra hot water carries none and stands
empty while nothing is held. The controller's *Extra hot water left* (*Extra
varmvatten kvar*, 61503) did not follow it the one time it was tried, on an
i255: it stayed at zero
through seven minutes of the control held at one hour, with every write
reaching the unit, so the control does not pass 61503 off as the time left.
Whether the controller starts its timer again at each of the keepalive's
rewrites is not known; the control is written and kept alive like the others.
While nothing is held the hot water mode shows
the pump's own program from 61500 rather than *Släpp styrningen*, which stays
in the list for letting go. When 61500 could not be read, in a Modbus round
that failed or one where the block did not answer, the hot water mode shows
*unknown* rather than *Släpp styrningen*, so the history never shows a release
that did not happen. Choosing a mode, from an automation or an energy
manager, writes it as before, whatever the list showed. The zone mode and the
hot water setpoint have no such value:
the stored heating mode (61542) has other modes than the zone mode, and 62001
is the hot water stop temperature, not the setpoint. With no stored mode to
show, the zone mode and the price mode show *Släpp styrningen* while idle,
whatever Modbus does. The sensor **Active overrides** (*Aktiva
överstyrningar*) counts what Home Assistant holds right now and lists each
held register with its value and when that value was set, *satt* and
*set_at*: the write that put it in force or last changed it. The keepalive's
rewrites of the same value do not move it, so the sensor gets a new state in
the history only when an override is set, changed or released, not every
minute; when a write last reached the controller is on each control, as
*last_written*. The overview shows the sensor, with **Release all control**
beside it, whenever anything is held.

The stored settings in the 61500 block are exposed read only and never written.
CTC states plainly that the number of write cycles there is limited and that
frequent writing can destroy the controller. That is enforced in code, not left
to convention: the Modbus client refuses any address outside the control
registers before it even takes the connection, and the test suite checks that a
write to 61500, 61503 or 62000 never reaches the wire.

## Actions

Three actions, for automations, scripts and energy managers, under *Developer
tools, Actions* as everywhere else. None of them writes anything outside the
control registers, and the first writes nothing at all.

- **`ctc_ecozenith.release`** lets go of the controls it is given, one or
  several number or select entities of this integration, in `entity_id`.
  Nothing is written: Home Assistant stops writing those registers, the
  controller goes back to its own setting within about five minutes, and every
  other override stays as it is. An energy manager can drop a power limit
  without letting go of the hot water. An entity that is not a control of a
  loaded heat pump is refused, and then nothing in the call is released. In
  the example, put your own model in place of `i255`; an installation from
  before the translations, or one in Swedish, has
  `number.ctc_ecozenith_i255_styr_max_elpatron_nedre` instead.

  ```yaml
  action: ctc_ecozenith.release
  data:
    entity_id:
      - number.ctc_ecozenith_i255_control_max_lower_immersion_heater
  ```

- **`ctc_ecozenith.reread_menu`** reads the display's menu again, the same
  reading as *Read the display's menu again* in the options, for the heat pump
  named in `config_entry_id` or, without it, for every loaded heat pump that has
  a display. Pages switched off stay off and new pages are read from then on;
  the integration reloads to pick them up. It moves the physical panel and puts
  it back, so it is not something to run on a timer. A panel that is in use
  right now, because the integration is reading it or walking it, is answered
  with an error at once rather than waited for, and a reading that does not get
  the whole menu changes nothing, as does one whose heat pump is reloaded or
  unloaded while its menu is read. Over several heat pumps, an answer where
  some were read and others not names all three kinds, those read, those whose
  panel was in use and those whose menu was not read whole, so the others can
  be asked for again with `config_entry_id` rather than walking a panel that
  was just read, and one where none was read keeps the panels in use apart from
  the menus not read whole. An entry set up on Modbus alone has no menu and is
  refused.

- **`ctc_ecozenith.cop_sample`** writes the two lifetime energy counters down as
  a sample of the coefficient of performance now, rather than at the next six
  hourly sample, for the heat pump in `config_entry_id` or for every one that
  has the counters, and answers with them per entry: `heat_out_kwh`,
  `energy_in_kwh`, `energy_in_from` (`display` or `modbus`), `read_at`, the
  moment the counters were read off the panel, `last_sample_at` and
  `recorded`, whether this call kept a new sample. A sample is the counters as
  they were last read, never the clock, so a reading that is already a sample
  is not taken twice and a counter page that has not been reached lately gives
  none. The sample also keeps the immersion heater's counter as it stood at
  that moment, for the share of auxiliary heat, where it was read then.

  ```yaml
  action: ctc_ecozenith.cop_sample
  response_variable: counters
  ```

## Attribute names

The attributes automations are most likely to read had Swedish names, as the
entities had before they followed Home Assistant's language. Each now has an
English twin, and both are sent in this version with
the same value, except where the table says otherwise. **The Swedish names will
be removed in the next major version**, so templates, automations and energy
managers that read them should move to the English names now.

| Swedish name, until the next major version | English name | On |
|---|---|---|
| `skäl` | `reason` | the coefficient of performance and auxiliary heat share sensors |
| `underlag` | `basis` | the coefficient of performance and auxiliary heat share sensors |
| `dygn i underlaget` | `days_in_basis` | the coefficient of performance and auxiliary heat share sensors |
| `avgiven värme kWh` | `heat_out_kwh` | the coefficient of performance sensors |
| `tillförd energi kWh` | `energy_in_kwh` | the coefficient of performance sensors |
| `tillförd energi ur`, `displayen` or `Modbus 62341` | `energy_in_from`, the same text | the coefficient of performance sensors |
| `elpatron kWh` | `immersion_kwh` | the auxiliary heat share sensors |
| `kompressor kWh` | `compressor_kwh` | the auxiliary heat share sensors |
| `kod` | `code` | a status sensor answering a code it has no label for, and *Last alarm* |
| `källa` | `source` | every display row |
| `sida` | `page` | every display row |
| `skärm` | `screen` | every display row |
| `senast läst` | `read_at` | every display row |
| `senast skriven` | `last_written` | the controls, while an override is in force |
| `gäller till` | `valid_until` | the controls, while an override is in force |
| `styrning aktiv`, `ja` or `nej` | `control_active`, `true` or `false` | the controls |
| `pumpens värde` | `pump_value` | the controls with a stored setting beside them, also while an override is in force |
| `episoder`, each with `kod`, `text`, `start`, `slut`, `utetemperatur` | `episodes`, each with `code`, `text`, `start`, `end`, `outdoor_temperature` | the binary sensor *Alarm* |
| `visat` | `shown` | the binary sensor *Display alarm* |
| `senast bekräftad` | `last_confirmed` | the binary sensor *Display alarm* |
| `bekräftad` | `confirmed` | the binary sensor *Display alarm* |
| `styrningar`, each with `namn`, `register`, `värde`, `satt` | `overrides`, each with `name`, `register`, `value`, `set_at` | the sensor *Active overrides* |

Values that are text, such as a reason, what a figure rests on, where the
consumed energy was read or a held control's name in *overrides*, stay in
Swedish under both names whatever language Home Assistant speaks, like the
states; see [Language](#language). The other attributes keep their Swedish
names for now.

## Known limits

- **One Modbus master.** The controller accepts a second TCP connection and then
  resets it as soon as that client sends anything, which looks exactly like the
  unit being offline. Do not point a second tool at it while this is running.
  That includes a `modbus:` block in `configuration.yaml` pointing at the same
  unit, which has to be removed before this integration can connect. The
  integration owns its own session accordingly: the library is told never to
  reconnect by itself, a client is closed before another is built, and a
  connection that is found gone is given up at once and knocked on again,
  after the controller's settle time, on the next poll.
- **A register the model lacks is answered with silence.** The controller
  simply does not reply, so such a block costs a full timeout every poll. After
  three polls in a row where a block stayed silent while the rest answered, it
  is left out until Home Assistant restarts, and the log says so once. A poll
  where nothing answers, or where the connection is lost halfway, ends there
  and the readings go unavailable, rather than waiting out every remaining
  block against a dead line.
- **CTC sets the pace.** The controller cannot pipeline and documents an update
  rate of one second, so requests are serialised, spaced out, and capped at a
  hundred registers each. It also needs a moment after the socket opens before
  it answers, so the first poll after a reconnect is delayed deliberately.
- **The web server drops connections above roughly five in flight.** The client
  keeps three.
- **A slow answer costs one reading, not all of them.** A reading that times out
  is asked for once more, with longer patience; a tap never is, since a tap that
  did land would move the panel twice. A harvest that fails keeps the readings it
  already had and tries again in five minutes instead of thirty. Only after three
  failed harvests in a row are the display's readings called unavailable. A
  single page that cannot be reached costs that page alone: its readings keep
  the moment they were last read, shown as the attribute *read_at*, and go
  unavailable once that moment is three intervals old, while the other pages
  go on as usual.
- **Absent hardware still answers.** The controller replies for ten heat pumps
  and four heating systems whatever is actually installed, with plausible
  numbers. Readings marked as missing use CTC's own markers, plus or minus 9999
  and 10000 and 32767, and a 32 bit counter of all ones, which are filtered out
  after the sign is applied, so a negative marker cannot pass as a temperature.
  A display row that reads the marker and has never read as a number, the brine
  temperatures on an air to water unit for instance, gets no entity until it
  first does; the first harvest in which it reads as a number adds the entity,
  with no reload. A row that has read as a number once, a true zero included,
  keeps its entity through any spell of the marker, an outdoor unit switched
  off for a week among them. The integration remembers which rows have, beside
  what it has seen. An entity from an earlier version for a row the display's
  menu no longer has on its page is removed from the registry, with a line in
  the log; one for a row that is still on its page, or on a page the menu does
  not know, is left alone. A row the display draws only some of the time stays
  on its page too: on an i255's heat pump page one slot shows the compressor's
  speed while it runs and a status while it stands still, and a reading of the
  menu sees only one of them, so every row a page has shown once is kept there
  by its place and read whichever the slot shows. That includes the entities an earlier version made
  for rows that have only ever read the marker: they stay in the registry as
  unavailable, and can be deleted in the entity's settings dialog, since the
  integration no longer provides them. A fresh installation never gets them.
- **The web interface is undocumented.** A firmware update can change it. Modbus
  is documented and will keep working.
- **The coefficient of performance has not been seen on an i360 yet.** Its
  stored operation data page counts delivered energy but not the energy
  consumed, as CTC's manual has it, so the consumption comes from Modbus
  register 62341 there. On i360 units that send the anonymous statistics the
  page and both counters have been recognised, the delivered heat on the
  display and the consumption in 62341, but no figure from one has been seen
  so far.

## Anonymous statistics

The integration sends one report per day to <https://stats.rnet.se>. The
client shared by the beolink integrations, `stats.py`, puts in it: a random id
created when the integration was installed, so that the daily reports can be
kept apart and erased on request (the server keeps it only as a hash, and it
says nothing about who you are), which
version of the integration you run, your Home Assistant version, installation
type and Python version, the language and the country you have set in Home
Assistant itself, an approximate position rounded to about 11 km, how many
entities and devices the integration created, a hash of Home Assistant's
instance id so that beolink integrations on the same installation can be seen
to be on the same installation (the id itself never leaves the house, and the
server hashes the hash again before storing it), how many warnings and errors
the integration's own logger wrote since the previous report, never a message,
and the config entry's state by Home Assistant's own name for it, such as
loaded or waiting to be set up again, never the reason.

This integration's own part, `stats_extra.py`, adds which transports are in
use, whether control is enabled, how many display pages are harvested and how
many register reads failed. It says how many pages the display's menu has, so
that no page harvested because nothing is ticked can be told from no page
harvested because the menu was never read, and whether the last reading of the
menu since Home Assistant started recognised the home screen and got into the
operation data menu, so that a menu that cannot be read says at which step.
It also says whether a coefficient of performance
is possible and, if not, why: whether the row with the unit's operating hours
was recognised on the history page, whether the delivered heat counter was
recognised there too, whether the energy consumed comes from the display or
from Modbus (for Modbus, whether register 62341 has
answered at all since Home Assistant started, judged on the raw words so that
CTC's marker for a counter that is not fitted still counts as an answer),
whether each of those two counters actually handed over a number at the last
read, and which of three things is in
the way, too little energy counted so far, a counter standing still although
the unit has been running, or a quotient no heat pump could produce. It says
whether the heat pump's page carries the two power rows at all and whether they
handed over numbers at the last read, which a heat pump switched off does not,
and whether the figure rests on the integration's own sums of the two power readings rather than
on the machine's counters, because a figure from the rougher basis is not to be
compared with one from a counter as if they were the same measurement. Yes or no
each time, never which page or what is on it.

It also sends what the installation is made of and how well it performs: the
indoor unit's model, the outdoor unit's model, the firmware in the display, in
the heat pump's control board and in the control unit, the week the machine was
built, and its coefficient of performance over the last day, over a rolling
year, over the machine's first year and over its lifetime. The figures over a
week and a month are not sent.

An indoor unit the integration does not know yet is reported as "other", which
says nothing about what turned up. For those alone the report adds a flag
named after the family code in the name of the display's settings file, such
as `family_ezi4xx`, so a new kind of controller is seen when its first owner
installs the integration rather than when somebody files an issue. The code is
sent only when it is two to sixteen lowercase letters and digits and nothing
else; a known model sends no such flag.

The flags and the numbers the report can carry are two closed lists next to
the code that builds it, `FEATURE_KEYS` and `METRIC_KEYS` in `stats_extra.py`,
with one built key beside them: the `family_` flag above, sent only for a model
reported as "other" and only when the code after `family_` matches
`^[a-z0-9]{2,16}$`. A test builds the report with everything set and fails the
moment it grows a key that is not on the two lists, and the family flag is held
to that pattern by tests of its own. This text, the consent text under
Configure and the list at <https://stats.rnet.se/integritet> are kept to the
lists and the one built key, so the promise and the report cannot drift apart
unnoticed.

CTC writes a serial number as three groups of four digits: which product it is,
the year and week it was made, and a sequence number. The first two groups are
sent, because they describe a production run. **The sequence number, which is
what identifies your particular machine, is not sent.**

It never sends a name, an address, an exact position, a full serial number, an
entity name or a page name, and your IP address is not stored or used to guess
where you are. **One measurement can leave the house, and only when it is wrong:**
where a lifetime counter stands still although the unit has been running, or where
the two counters give a quotient that cannot be right, the two totals in kilowatt
hours are sent along with the flag. They are what separates a counter reading zero
from one that was never read, and without that nothing about such an installation
can be fixed. A machine that has merely not counted far enough yet sends no
numbers, only the flag saying so. The position is rounded inside your
own installation before anything is sent, and rounded again on the server, so a
finer value does not exist in the database. Reports are stored per date, never per
time of day, so they cannot show when anyone is home. What the backend accepts
is a closed list with a pattern per field, so free text cannot be stored even
by mistake. The statistics sit behind a login at <https://stats.rnet.se> and are
seen only by whoever runs the service.

The point is to know which versions are actually in the field, which parts are
worth maintaining and whether something is failing on units other than mine.

The first report after each start of the integration is also written out in
full to Home Assistant's log at info level, so you can read exactly what left
your installation before deciding whether to keep it on.

To opt out: *Settings, Devices and services, CTC Local Easyconf, Configure, Send
anonymous usage statistics.* Switching it off stops the reporting and marks the
installation as having stopped, and no further report is taken until it is
switched back on. What has already been sent stays, and that is a deliberate
choice: the reports are anonymous, carry no free text and are stored per day, and
which installations have existed is what the statistics are for, since an
integration falling out of use says as much as one coming into use. If you want
what was sent erased anyway, ask whoever runs the service and the installation's
row and all its daily rows are deleted. The full list of fields and the reasoning:
<https://stats.rnet.se/integritet>. The code that builds the report is
`stats_extra.py`, and the client that sends it is `stats.py`.

## Debug report

Some questions cannot be answered from the anonymous statistics, by design, nor
from one diagnostics file: whether a coefficient of performance can be worked
out on a model whose lifetime counters never move turns on what its display
shows while the compressor runs, hour after hour. For that the integration can
send everything it knows, to the developer, while you want it to.

It is **off** until you switch it on, and once on it stays on until you switch
it off. There are two places for the one setting: the tick box **Report all
values to the developer** (*Rapportera alla värden till utvecklaren*) last on
the CTC page's controls tab, under *Troubleshooting*, which is a switch of
that name on the heat pump's device, and the option of the same name under
*Configure*. Switching it on or off takes effect at once and
does not reload the integration: a reload closes Modbus, and a set-up that
meets the heat pump's one Modbus place still taken waits for it.

While it is on, a report goes to <https://stats.rnet.se> a few seconds after it
is switched on, two minutes after each start of the integration, also while
the integration is still waiting for the heat pump to answer, and every 30
minutes after that. Nothing is collected or sent while it is off. A report
holds:

- exactly what *Download diagnostics* gives (see
  [Reporting a problem](#reporting-a-problem)): every value the display and
  Modbus last gave, when each display row was last read, how the harvest and
  the readings of the menu went, the Modbus block plan, the counters behind the
  coefficient of performance, the latest transitions and the alarm log;
- the integration's own warnings and errors from its log since the last report
  the server stored, at most 200 lines of at most 2 000 characters each. An
  error's traceback is not sent, only its type and message, since the lines of
  a traceback name the files of your installation;
- the random id the anonymous statistics already use for this installation,
  read from `config/.storage/ctc_ecozenith.stats` and made there where it is
  missing, so that the reports sit under the same installation; and the
  versions of the integration and of Home Assistant.

Before anything leaves the house it is washed as the diagnostics file is: the
address, the device key, the MAC address, the entry's title and the device's
link and identifiers are taken out, every IPv4 and MAC address in any text
becomes a placeholder, the display's host name loses its digits, and the
serial number is cut down to its product and week groups wherever it is spelt.
The log is washed with the names of every heat pump of the integration, and
the server washes all of it again before storing it. A report is at most a
megabyte; the oldest log lines give way first.

The reports are kept for 14 days and then deleted, sit behind the login at
<https://stats.rnet.se> under the installation they came from, and are seen
only by whoever runs the service. They are erased on request. The debug report
is independent of the anonymous statistics: either can be on without the
other. The code is `debug_report.py`, and what the integration logs about it
is on debug level, apart from one info line when it starts.

## Reporting a problem

When the display's menu cannot be read, the log says so once per start of Home
Assistant, on warning, with the step the walk stopped at. A walk that read the
menu says so on info, which Home Assistant leaves out of the log unless debug
logging is switched on for the integration.

Open an issue on GitHub; the form asks for the diagnostics file, which is what
makes a report answerable. Download it under *Settings, Devices and services,
CTC Local Easyconf*, the three dots next to the heat pump, *Download
diagnostics*. Home Assistant offers it only while the entry is loaded: while
the integration is still waiting for the heat pump to answer, the menu has no
such item. Copy the reason shown under the entry instead, which names a Modbus
place held by another client, and add the log lines. With a long-lived access
token the file can still be fetched from `/api/diagnostics/config_entry/`
followed by the entry's id, since the integration builds it in that state too.
It holds the stored menu and which pages are ticked and
harvested, how many readings of the menu this run has spent and how far the last
one got (the home screen recognised, the operation data tile found, the menu
entered, or what the display said when it stopped answering), when each display
row was last read and why the last harvest gave way, the Modbus block plan and
the blocks your model turned out not to have, codes the integration has no
label for, which controls are in force, the energy samples behind the
coefficient of performance, the latest transitions and the alarm log.

Before you get it, the address, the device key (the address the entry was set
up with, which stays when the heat pump moves), the MAC address, the entry's
title, the device's link and identifiers are taken out, the address and the
device key are replaced wherever a text spells them, as is every IPv4 address
and MAC address in any text, and the serial number is cut down to its product
and week groups wherever it is spelt, the display's host name included. The
code is `diagnostics_data.py`; read the file through before you attach it all
the same.

Where the developer asks for the values over a few hours rather than one file,
switch on the [Debug report](#debug-report) for that while, and off again.

## Removing the integration

*Settings, Devices and services, CTC Local Easyconf*, the three dots next to
the heat pump, *Delete*. Home Assistant removes the device and its entities,
and the integration deletes what it kept for that heat pump: the values it has
seen, the last harvest of the display, the energy samples behind the
coefficient of performance and the alarm log, and its notices in the repairs
view. The CTC page leaves the sidebar with the last heat pump. Nothing is
written to the pump on the way: whatever Home Assistant held, the controller
lets go of by itself about five minutes after the last write.

Three things stay behind. The card script's entry among the dashboards'
resources, `/ctc_ecozenith/ctc-ecozenith-card.js`, is deleted under *Settings,
Dashboards*, the three dots at the top right, *Resources*, which shows with
advanced mode switched on in your user profile; the integration's cards on a
dashboard of your own are deleted from that dashboard. What the anonymous
statistics have already sent stays either way: removing the integration stops the
reports, and so does switching *Send anonymous usage statistics* off under
*Configure*, which also marks the installation as having stopped. Erasure is on
request, from whoever runs the service. A debug report already sent is
deleted by the server 14 days after it came, or sooner on request. And the file `config/.storage/ctc_ecozenith.stats`, which
holds the installation's random id for those reports, one for all heat pumps
together: delete it by hand once the last heat pump is removed, or an
installation of the integration later on reports under the same id.

Then remove the files, through HACS (the integration's three dots, *Remove*)
or by deleting `config/custom_components/ctc_ecozenith`, and restart Home
Assistant. If nothing else uses them, switch Modbus TCP and the web interface
off again at the panel; neither has a login (see
[Network and exposure](#network-and-exposure)).

## Releases

Every release is made from a tag that names the version in `manifest.json`,
on a commit that is on `main`. The release workflow checks both, and it waits
for the whole validation on that commit, not only for the tests: hassfest,
the HACS checks, ruff, the test matrix, and a real Home Assistant on the
houses' release and on the floor in `hacs.json`. Only then does it publish the
release HACS installs from. `main` and the tag go up in one push, both or
neither:

```sh
git tag -a --cleanup=whitespace v0.21.0 -F notes.md && git push --atomic origin main v0.21.0
```

Headings with `#` in `notes.md` need `--cleanup=whitespace`: without it,
`git tag` strips every line that starts with `#` as a comment. The notes are
the tag's message when it has more than one line, else the subjects of the
commits since the tag before (`scripts/release_notes.py`). A tag that is not
on `main` stops at the check; push `main` and re-run the failed jobs. A fix
for something that stops a sensor is released the same day, not saved for the
next version.

## Tests

The suite in `tests/` runs without Home Assistant installed and is what CI
runs: `pip install -r requirements-test.txt` and `python -m pytest -q`. It
covers the protocol decoding, the menu reading, the dashboard layout and the
report, all against captured fixtures from the two units. Nothing in it talks
to a real heat pump or to anything outside the process: the sockets it opens
are on loopback inside the test process, in `tests/test_pymodbus_surface.py`,
where a scripted controller lets the whole chain from pymodbus's real surface
down to the register words be driven end to end, and in
`tests/test_modbus_probe.py`, where the probe meets ports that answer, reset or
say nothing as told.

The `tests/test_homeassistant*.py` files need more than the test requirements.
They drive the integration through a real Home Assistant core, from set-up to
reload to the `number` service call that writes a control register, with both
the Modbus client and the display replaced by stand-ins, and check what a unit
test cannot: that a failed set-up closes its one Modbus socket, that a reload
never has two clients open at once, that a setpoint reaches the right register
as the right raw value and keeps being written until it is released, and that
the menu is tried three times in total across reloads, not three times per
reload, among others. They need `pytest-homeassistant-custom-component`, which
pins one Home Assistant release and everything that release depends on, so
they belong in a virtual environment of their own rather than beside the
ordinary suite's requirements. They skip themselves, with a note, where the
plugin is missing:

```sh
pip install pytest-homeassistant-custom-component
python -m pytest -q tests/test_homeassistant*.py
```

The whole suite runs in that environment too, with the same `python -m pytest
-q`. Two things make that so. The plugin's own autouse fixtures are async and
need pytest-asyncio in auto mode, or every test in the session errors at
set-up; `tests/conftest.py` switches the mode on wherever pytest-asyncio is
loaded, so there is nothing to pass by hand. And `tests/ha_stub.py`, which
stands in for Home Assistant when it is not installed, steps aside when it is:
the modules then load against the real classes, the keepalive tests record the
real timer helper instead of the stand-in's, and the tests that build a
coordinator directly skip themselves, since a real core's coordinator wants a
running instance behind it. Leave pymodbus out of that environment: the plugin
blocks sockets, and `tests/test_pymodbus_surface.py`, which skips itself
without pymodbus, does not ask for its loopback socket back the way
`tests/test_modbus_probe.py` does.

The suite runs against stand-ins, so what only a real Home Assistant would
refuse, a moved import or a selector the real selector does not accept, is
checked by `scripts/smoke_real_ha.py`. It imports every module of the
integration, runs every Home Assistant import as it is written, inside
functions and try blocks too, looks up every attribute of a Home Assistant
module the source reaches for and holds the calls of what it finds to their
real signatures, and builds every form that needs no network, the CTC page's
Lovelace class and `services.yaml`, under whichever release is installed:

```sh
pip install homeassistant==2026.9.4
python scripts/smoke_real_ha.py
```

CI runs it against the release the maintainer's houses run, against the floor
`hacs.json` promises, and every Monday against the newest release with
`--strict`, which also fails on Home Assistant's notices that the integration
uses something on its way out. CI further runs the suite on Python 3.13 and
3.14 against four pymodbus versions (the manifest's floor, the two Home
Assistant pins in the floor in `hacs.json` and in the houses' release, and the
newest), the whole suite under `pytest-homeassistant-custom-component`, ruff
with the rules in `ruff.toml`, hassfest and the HACS checks.

### A display page from a new model

A model the integration has not met is most often a display page whose
readings get the wrong captions. The pairing is tested against real pages:
four of them, from an i255 and an i550 Pro, lie in `tests/fixtures` as
`widgets_<model>_<screen>.json`, each the screen's widgets with every caption
resolved through the display's text catalogue, and `REAL_PAGES` in
`tests/test_modbus_and_catalogue.py` says which reading each caption names, as
read off the panel itself. A page of a new model comes in the same way. With
nobody at the panel, `CtcWebClient.async_widgets(screen)` in `web_api.py`
reads the screen's definition (`/wp/<screen>`), its values (`/vars/<screen>`
and `/vars/glob`) and its captions (`/txt/<language>/<id>`), one request at a
time, and presses nothing; the screen numbers of each page are in the stored
menu of the diagnostics file. Write the widgets out as
`{"screen": ..., "widgets": [...]}` with the fields the four fixtures have, add
the page and the captions the panel prints to `REAL_PAGES` and the file to the
list in `tests/test_fixture_hygiene.py`, and run the pairing over every
fixture, the old ones too: a rule that puts one layout right can push another
out of line, which is what the first attempt at 0.15.1's fix did. Before you
send a fixture in, take your own address, MAC address, host name and serial
number out of it by hand. `tests/test_fixture_hygiene.py` only catches what
follows a pattern: a private IPv4 address that is not on its list of made-up
ones, a MAC address other than the made-up `02:00:00:00:00:0x`, the display's
host name `CTC` and four digits other than `CTC0001`, and a serial number whose
last group is not `0001`. It does not see a public address, a name from your
own DNS or a serial number written some other way.

### The quality scale and the Home Assistant floor

`custom_components/ctc_ecozenith/quality_scale.yaml` is Home Assistant's
integration quality scale as a checklist, every rule with done, todo or exempt
and why. hassfest reads that file only for Home Assistant's own integrations,
so `tests/test_quality_scale.py` runs hassfest's checks for the rules marked
done and keeps the manifest from claiming a tier before every rule of it is
met. `tests/test_ha_floor.py` holds the Home Assistant version in `hacs.json`
to the one stated under [Install](#install) and to `NEEDS`, the Home Assistant
names that set the floor, each found by name in the source; a need no longer
found fails there until the smoke run under the release before it confirms
that it is gone and its line is taken out. The smoke run against the floor
finds a newer module, function, class or attribute wherever the source reaches
for it, but not a method on an object (`hass.config_entries`, a `ConfigEntry`,
a selector) or the arguments it takes: code that starts using one newer than
the floor adds its line to `NEEDS` by hand.

## Licence

[Apache License 2.0](LICENSE).

CTC, EcoZenith and EcoAir are CTC's names. The icon in `brand/` and the
sidebar icon of the CTC page are CTC's own logo, used to show which make of
heat pump this is about; the integration is neither made nor endorsed by CTC.
