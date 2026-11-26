# Changelog

The notes of a release are its tag's message, as the README says under
Releases. This file collects what is on its way into the next release.

## Unreleased

### Added

- The daily report sends the count of failed register reads split in two as
  well, `line_errors` and `block_errors` beside the unchanged total: rounds
  that lost the connection, and register blocks that stayed silent while the
  rest answered. The two call for opposite answers and one total hid which it
  was. A block the model lacks is given up on after three rounds and then
  costs nothing, so it cannot reach three figures a day on its own, while a
  line that keeps dropping is counted again every round. *Download
  diagnostics* carries the same two counts.

### Fixed

- The confirmation of a unit that turned up by itself no longer calls
  `_set_confirm_only`, which Home Assistant 2026.11 deprecated as a no-op
  whose flag was never read. Nothing changes in the form.
- The weekly smoke test against the newest Home Assistant starts again: since
  2026.11 the entity registry waits, while it loads, for the config entries,
  so the smoke test now loads the registries and the config entries side by
  side as Home Assistant's bootstrap does. It had stopped before testing
  anything on 9 and 16 November, which is also why the deprecation above was
  not reported.

## 0.31.0 (2026-11-22)

### Added

- **The outdoor temperature beside the coefficient of performance**, in the
  same chart on an axis of its own: the coefficient on the left, °C on the
  right. On an air to water heat pump the one follows the other closely, and
  the two are now read off one picture. Both charts of the coefficient of
  performance on the performance tab have it: *COP over the last day* draws
  the two as lines over 24 hours, and *COP per day* a bar a day over a month
  with the day's mean outdoor temperature as a line over the bars. Holding
  the pointer over a chart, or touching it, shows the moment's or the day's
  figures together.
- The charts are the integration's own card, custom:ctc-ecozenith-cop-chart
  in www/ctc-ecozenith-chart.js. Home Assistant's own graphs cannot draw two
  units in one chart: its history graph gives each unit a chart of its own,
  one under the other, and its statistics graph draws both on one axis with
  no unit. The card reads the figures as those graphs do, the history over
  the websocket for the day and the long term statistics for the month, and
  again every five minutes while the page is open.
- The outdoor temperature rides along and nothing more: a pump with no
  coefficient of performance gets no chart of the temperature alone, and the
  chart hides with its own lines. A temperature that moves a degree over a
  day keeps an axis at least 4 °C high, so a step of the sensor is not drawn
  as a change of weather.

## 0.30.0 (2026-11-13)

### Added

- **The coefficient of performance over the last day** on the performance
  tab, under the compressor's day: a line over 24 hours, as the graphs above
  it. On a heat pump whose energy counters give a coefficient of performance
  it is the pump's own figure for the last day; on one whose counters give
  none, an EcoAir 600M among them, it is the integration's estimate of the
  day in its place, under the estimate's own name, so the line says what it
  is. Never both: a pump that reports a figure is not drawn a second, rougher
  line beside it. Which of the two is read off the states as the page is
  built: any span of the counters' figure with a number makes it the pump's
  own.

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
