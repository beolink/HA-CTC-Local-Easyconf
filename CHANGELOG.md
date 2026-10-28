# Changelog

The notes of a release are its tag's message, as the README says under
Releases. This file collects what is on its way into the next release.

## 0.29.1 (2026-10-28)

### Fixed

- The sensor *Last display harvest* now follows a live read that read
  nothing. Such a try leaves the data alone and so told the coordinator's
  listeners nothing, and the sensor kept the account of the last read that
  went through: on VSH, with the panel on the home screen after the live page
  was picked, it said no try had failed and gave no reason, while the
  diagnostics counted nine skips because the panel showed page 0. The
  coordinator now tells a channel of its own about such a try, and the sensor
  listens there; the integrators behind the coordinator's listeners still
  never meet the same reading twice.

## 0.29.0 (2026-10-26)

### Added

- **A live page.** One harvested page can be the page the panel rests on
  between two walks. The walk reads it last and ends on it instead of putting
  the panel back on the home screen, and the page is read off the panel every
  live interval, 60 seconds by default and never under 30, with two small GET
  requests and never a tap, for as long as the panel shows it. Its rows, and
  the estimated coefficient of performance built on the heat pump page's two
  powers, then follow the machine by the minute instead of by the half hour.
  Asked for on 2026-10-26, after taking *Driftinfo Värmepump* out of the
  harvested pages had only made the page's rows unavailable: a display page
  is read by walking the panel there, so a page nobody walks to is never
  read. Two new options, *Live page* and *Live interval*, under Configure and
  on the set-up form, where the heat pump's page, the one with the delivered
  heat and the supplied power, is offered already picked. An existing
  installation is not parked: its options hold no live page until one is
  picked. Choosing a page ticks it among the pages to read.
- The parking is the walk's own taps: the walk reads the live page last and
  finds the panel on it when it would put the panel back, so the only new
  activity on the display is that the walk ends on the live page rather than
  on the home screen. A live read presses nothing, so somebody at the panel is
  never interrupted; the reads pause until the next walk parks the panel
  again. A live read notes the page's alarm text too, which may open or
  confirm an alarm episode but never closes one; that stays with the walk,
  which reads every page that can print an alarm.
- The sensor *Last display harvest* says, with a live page, when it was last
  read without a press, which page the panel showed at the last try and why a
  try read nothing. The diagnostics download carries the same under
  `display.live`, with the number of reads and what `/vars/menu` answered, and
  the debug log has one line per read, so a read's moment can be set beside
  the panel's backlight. Whether a read lights the backlight is not known;
  the README says what to look for.
- The source line under a live page's row on the CTC page says that the row
  is read live, and how often.
