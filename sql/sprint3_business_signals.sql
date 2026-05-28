ALTER TABLE articles
ADD COLUMN IF NOT EXISTS signal_type TEXT,
ADD COLUMN IF NOT EXISTS why_it_matters TEXT,
ADD COLUMN IF NOT EXISTS business_implication TEXT,
ADD COLUMN IF NOT EXISTS suggested_action TEXT,
ADD COLUMN IF NOT EXISTS target_persona TEXT,
ADD COLUMN IF NOT EXISTS urgency INTEGER;

CREATE INDEX IF NOT EXISTS idx_articles_signal_type ON articles(signal_type);
CREATE INDEX IF NOT EXISTS idx_articles_urgency ON articles(urgency DESC);
