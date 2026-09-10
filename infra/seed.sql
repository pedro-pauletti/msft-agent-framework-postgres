-- =============================================================================
-- FiberOps - sample schema and seed data
-- =============================================================================
--
-- Scenario: a fictional telecom operator monitoring its optical fiber backbone.
--
--   sites         POPs / stations where fiber terminates
--   fiber_links   optical links connecting two sites
--   fiber_alerts  alarms raised by the monitoring system on a link
--   alert_notes   free-form notes written by field technicians
--
-- `alert_notes` is deliberately the easiest table to write to: it is where the
-- agent will add its findings. `fiber_alerts.status` is the field the agent
-- will UPDATE.
--
-- This script is idempotent: running it twice rebuilds everything from scratch.
-- =============================================================================

DROP TABLE IF EXISTS alert_notes;
DROP TABLE IF EXISTS fiber_alerts;
DROP TABLE IF EXISTS fiber_links;
DROP TABLE IF EXISTS sites;

-- -----------------------------------------------------------------------------
-- sites: physical locations (points of presence)
-- -----------------------------------------------------------------------------
CREATE TABLE sites (
    site_id     SERIAL PRIMARY KEY,
    code        TEXT        NOT NULL UNIQUE,   -- short operational code, e.g. 'SPO-01'
    name        TEXT        NOT NULL,
    city        TEXT        NOT NULL,
    state       CHAR(2)     NOT NULL,
    latitude    NUMERIC(9, 6),
    longitude   NUMERIC(9, 6)
);

COMMENT ON TABLE  sites IS 'Points of presence (POPs) where optical fiber terminates.';
COMMENT ON COLUMN sites.code IS 'Short operational site code, e.g. SPO-01.';

-- -----------------------------------------------------------------------------
-- fiber_links: an optical link between two sites
-- -----------------------------------------------------------------------------
CREATE TABLE fiber_links (
    link_id         SERIAL PRIMARY KEY,
    code            TEXT        NOT NULL UNIQUE,  -- e.g. 'LNK-SPO-RIO-01'
    site_a_id       INTEGER     NOT NULL REFERENCES sites(site_id),
    site_b_id       INTEGER     NOT NULL REFERENCES sites(site_id),
    length_km       NUMERIC(7, 2) NOT NULL,
    capacity_gbps   INTEGER     NOT NULL,
    -- 'active'      = carrying traffic
    -- 'degraded'    = carrying traffic but below spec
    -- 'maintenance' = intentionally out of service
    status          TEXT        NOT NULL
                    CHECK (status IN ('active', 'degraded', 'maintenance')),
    commissioned_on DATE        NOT NULL
);

COMMENT ON TABLE fiber_links IS 'Optical links; each connects site_a_id to site_b_id.';

-- -----------------------------------------------------------------------------
-- fiber_alerts: alarms raised on a link by the monitoring system
-- -----------------------------------------------------------------------------
CREATE TABLE fiber_alerts (
    alert_id        SERIAL PRIMARY KEY,
    link_id         INTEGER     NOT NULL REFERENCES fiber_links(link_id),
    -- What the monitoring system detected.
    alert_type      TEXT        NOT NULL
                    CHECK (alert_type IN ('fiber_cut',
                                          'high_attenuation',
                                          'power_loss',
                                          'degraded_signal',
                                          'equipment_failure')),
    severity        TEXT        NOT NULL
                    CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    -- Lifecycle: open -> acknowledged -> resolved
    status          TEXT        NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'acknowledged', 'resolved')),
    -- Measured optical attenuation in decibels, when applicable.
    attenuation_db  NUMERIC(5, 2),
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    description     TEXT        NOT NULL,

    -- A resolved alert must have a resolution timestamp, and vice versa.
    CONSTRAINT resolved_alerts_have_timestamp
        CHECK ((status = 'resolved') = (resolved_at IS NOT NULL))
);

CREATE INDEX idx_fiber_alerts_status   ON fiber_alerts (status);
CREATE INDEX idx_fiber_alerts_severity ON fiber_alerts (severity);
CREATE INDEX idx_fiber_alerts_link     ON fiber_alerts (link_id);

COMMENT ON TABLE  fiber_alerts IS 'Alarms raised on fiber links. Lifecycle: open -> acknowledged -> resolved.';
COMMENT ON COLUMN fiber_alerts.attenuation_db IS 'Measured optical attenuation in dB (higher is worse).';

-- -----------------------------------------------------------------------------
-- alert_notes: field technician notes. This is the agent's main write target.
-- -----------------------------------------------------------------------------
CREATE TABLE alert_notes (
    note_id     SERIAL PRIMARY KEY,
    alert_id    INTEGER     NOT NULL REFERENCES fiber_alerts(alert_id) ON DELETE CASCADE,
    author      TEXT        NOT NULL,   -- technician name, or 'ai-agent'
    note        TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_alert_notes_alert ON alert_notes (alert_id);

COMMENT ON TABLE alert_notes IS 'Free-form notes attached to an alert. Written by technicians or by the AI agent.';

-- =============================================================================
-- Seed data
-- =============================================================================

INSERT INTO sites (code, name, city, state, latitude, longitude) VALUES
    ('SPO-01', 'Sao Paulo Central',    'Sao Paulo',      'SP', -23.550520, -46.633308),
    ('RIO-01', 'Rio Downtown',         'Rio de Janeiro', 'RJ', -22.906847, -43.172897),
    ('BHZ-01', 'Belo Horizonte North', 'Belo Horizonte', 'MG', -19.916681, -43.934493),
    ('CWB-01', 'Curitiba West',        'Curitiba',       'PR', -25.428954, -49.267137),
    ('POA-01', 'Porto Alegre South',   'Porto Alegre',   'RS', -30.034647, -51.217658),
    ('BSB-01', 'Brasilia Core',        'Brasilia',       'DF', -15.826691, -47.921822);

INSERT INTO fiber_links (code, site_a_id, site_b_id, length_km, capacity_gbps, status, commissioned_on) VALUES
    ('LNK-SPO-RIO-01', 1, 2, 429.50, 400, 'active',      '2019-03-14'),
    ('LNK-SPO-BHZ-01', 1, 3, 586.20, 200, 'degraded',    '2018-07-02'),
    ('LNK-SPO-CWB-01', 1, 4, 408.10, 400, 'active',      '2020-11-25'),
    ('LNK-RIO-BHZ-01', 2, 3, 434.00, 200, 'active',      '2017-05-19'),
    ('LNK-CWB-POA-01', 4, 5, 711.30, 100, 'active',      '2021-02-08'),
    ('LNK-BHZ-BSB-01', 3, 6, 716.40, 200, 'maintenance', '2016-09-30'),
    ('LNK-SPO-BSB-01', 1, 6, 873.00, 400, 'active',      '2022-06-17'),
    ('LNK-RIO-BSB-01', 2, 6, 933.60, 100, 'degraded',    '2015-12-01');

-- 25 alerts: a mix of open / acknowledged / resolved across all severities.
-- Timestamps are relative to now() so the data always looks "fresh".
INSERT INTO fiber_alerts (link_id, alert_type, severity, status, attenuation_db, opened_at, resolved_at, description) VALUES
    (2, 'high_attenuation',  'critical', 'open',          14.80, now() - INTERVAL '3 hours',    NULL, 'Attenuation above 14 dB on the Sao Paulo - Belo Horizonte span, near km 310.'),
    (8, 'degraded_signal',   'high',     'open',           9.20, now() - INTERVAL '6 hours',    NULL, 'Bit error rate climbing steadily on the Rio - Brasilia link.'),
    (1, 'power_loss',        'critical', 'open',          21.40, now() - INTERVAL '45 minutes', NULL, 'Total optical power loss detected on Sao Paulo - Rio. Suspected fiber cut near Resende.'),
    (5, 'high_attenuation',  'medium',   'acknowledged',   6.10, now() - INTERVAL '1 day',      NULL, 'Gradual attenuation increase on Curitiba - Porto Alegre. Field team dispatched.'),
    (3, 'equipment_failure', 'high',     'acknowledged',   NULL, now() - INTERVAL '9 hours',    NULL, 'Line amplifier at km 180 reporting temperature alarm.'),
    (4, 'degraded_signal',   'low',      'open',           3.40, now() - INTERVAL '2 days',     NULL, 'Minor signal degradation observed during the night window.'),
    (6, 'fiber_cut',         'critical', 'acknowledged',  32.00, now() - INTERVAL '5 hours',    NULL, 'Fiber cut confirmed during scheduled maintenance on Belo Horizonte - Brasilia.'),
    (7, 'high_attenuation',  'medium',   'open',           7.80, now() - INTERVAL '11 hours',   NULL, 'Attenuation drift on the Sao Paulo - Brasilia long haul.'),
    (2, 'equipment_failure', 'high',     'open',           NULL, now() - INTERVAL '30 hours',   NULL, 'Transponder card unresponsive at Belo Horizonte North.'),
    (8, 'power_loss',        'high',     'acknowledged',  18.90, now() - INTERVAL '4 hours',    NULL, 'Sharp optical power drop on Rio - Brasilia. Possible connector damage.'),

    (1, 'fiber_cut',         'critical', 'resolved',      30.10, now() - INTERVAL '12 days', now() - INTERVAL '11 days', 'Backhoe cut near Taubate. Splice completed and link restored.'),
    (3, 'high_attenuation',  'medium',   'resolved',       5.90, now() - INTERVAL '9 days',  now() - INTERVAL '8 days',  'Dirty connector at Curitiba West. Cleaned and re-seated.'),
    (4, 'degraded_signal',   'low',      'resolved',       2.80, now() - INTERVAL '20 days', now() - INTERVAL '19 days', 'Transient degradation during a thunderstorm. Self-recovered.'),
    (5, 'equipment_failure', 'high',     'resolved',       NULL, now() - INTERVAL '15 days', now() - INTERVAL '14 days', 'Power supply replaced on the Porto Alegre amplifier hut.'),
    (7, 'power_loss',        'critical', 'resolved',      25.60, now() - INTERVAL '30 days', now() - INTERVAL '29 days', 'Rodent damage to the duct near Anapolis. Section replaced.'),
    (2, 'degraded_signal',   'medium',   'resolved',       6.70, now() - INTERVAL '6 days',  now() - INTERVAL '5 days',  'Dispersion compensation module retuned.'),
    (6, 'high_attenuation',  'high',     'resolved',      11.20, now() - INTERVAL '25 days', now() - INTERVAL '23 days', 'Aging splice enclosure replaced at km 402.'),
    (8, 'fiber_cut',         'critical', 'resolved',      35.40, now() - INTERVAL '40 days', now() - INTERVAL '39 days', 'Road works severed the cable near Goiania. Emergency splice.'),
    (1, 'degraded_signal',   'low',      'resolved',       3.10, now() - INTERVAL '3 days',  now() - INTERVAL '3 days',  'Brief BER spike, no action needed.'),
    (3, 'power_loss',        'medium',   'resolved',       8.30, now() - INTERVAL '18 days', now() - INTERVAL '17 days', 'Patch panel port replaced at Sao Paulo Central.'),

    (5, 'fiber_cut',         'critical', 'open',          28.70, now() - INTERVAL '2 hours',  NULL, 'Suspected fiber cut on Curitiba - Porto Alegre near km 520. OTDR pending.'),
    (4, 'high_attenuation',  'medium',   'open',           7.10, now() - INTERVAL '16 hours', NULL, 'Attenuation above threshold on the Rio - Belo Horizonte span.'),
    (7, 'degraded_signal',   'low',      'acknowledged',   4.20, now() - INTERVAL '3 days',   NULL, 'Low priority signal quality alert. Monitoring.'),
    (6, 'equipment_failure', 'medium',   'open',           NULL, now() - INTERVAL '7 hours',  NULL, 'Cooling fan failure in the Brasilia Core rack.'),
    (2, 'power_loss',        'high',     'open',          17.30, now() - INTERVAL '20 hours', NULL, 'Optical power below threshold on Sao Paulo - Belo Horizonte.');

-- A few technician notes so the agent has prior context to read.
INSERT INTO alert_notes (alert_id, author, note, created_at) VALUES
    (1,  'ana.souza',    'OTDR trace scheduled for 14:00. Access to the duct requires permission from the highway authority.', now() - INTERVAL '2 hours'),
    (3,  'carlos.lima',  'Field team en route to Resende. ETA 90 minutes.',                                                    now() - INTERVAL '30 minutes'),
    (7,  'marina.reis',  'Cut is inside the planned maintenance window. Customer impact avoided via the Sao Paulo reroute.',   now() - INTERVAL '4 hours'),
    (11, 'ana.souza',    'Splice loss measured at 0.08 dB after repair. Within spec.',                                         now() - INTERVAL '11 days'),
    (21, 'joao.pereira', 'Awaiting OTDR equipment availability in Curitiba.',                                                  now() - INTERVAL '1 hour');
