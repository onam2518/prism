-- Additive rollout: install before the application that writes source_fields.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';
ALTER TABLE public.prism_contents
  ADD COLUMN IF NOT EXISTS source_fields jsonb NOT NULL DEFAULT '{}'::jsonb
  CHECK (jsonb_typeof(source_fields) = 'object');

-- Manual confirmations survive automatic replacement, including concurrent writes.
CREATE OR REPLACE FUNCTION public.prism_preserve_manual_meta()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
DECLARE k text; evidence jsonb; changed boolean; status text;
BEGIN
  FOR k, evidence IN SELECT * FROM jsonb_each(CASE WHEN jsonb_typeof(OLD.item_meta->'manual_fields') = 'object'
    THEN OLD.item_meta->'manual_fields' ELSE '{}'::jsonb END) LOOP
    IF k NOT IN ('summary', 'entities', 'intent', 'content_category') THEN CONTINUE; END IF;
    -- Only a later explicit human confirmation may replace that field.
    IF COALESCE((NEW.item_meta #>> ARRAY['manual_fields', k, 'confirmed_at'])::numeric, 0)
         > COALESCE((evidence->>'confirmed_at')::numeric, 0) THEN CONTINUE; END IF;
    NEW.item_meta := COALESCE(NEW.item_meta, '{}'::jsonb);
    changed := evidence->>'input_revision' IS DISTINCT FROM NEW.item_meta->>'input_revision';
    NEW.item_meta := jsonb_set(NEW.item_meta, ARRAY[k], COALESCE(OLD.item_meta->k, 'null'::jsonb));
    NEW.item_meta := jsonb_set(NEW.item_meta, '{manual_fields}',
      COALESCE(NEW.item_meta->'manual_fields', '{}'::jsonb) || jsonb_build_object(k, evidence));
    status := CASE WHEN changed THEN 'pending'
      WHEN OLD.item_meta->k IN ('[]'::jsonb, '""'::jsonb, 'null'::jsonb) THEN 'no_value' ELSE 'success' END;
    NEW.item_meta := jsonb_set(NEW.item_meta, '{meta_status}',
      COALESCE(NEW.item_meta->'meta_status', '{}'::jsonb) || jsonb_build_object(k, status));
    IF changed THEN
      IF NOT COALESCE(NEW.item_meta->'manual_review_required', '[]'::jsonb) ? k THEN
        NEW.item_meta := jsonb_set(NEW.item_meta, '{manual_review_required}',
          COALESCE(NEW.item_meta->'manual_review_required', '[]'::jsonb) || to_jsonb(k));
      END IF;
      IF NOT COALESCE(NEW.item_meta->'hold_fields', '[]'::jsonb) ? k THEN
        NEW.item_meta := jsonb_set(NEW.item_meta, '{hold_fields}',
          COALESCE(NEW.item_meta->'hold_fields', '[]'::jsonb) || to_jsonb(k));
      END IF;
    ELSE
      NEW.item_meta := jsonb_set(NEW.item_meta, '{hold_fields}',
        COALESCE(NEW.item_meta->'hold_fields', '[]'::jsonb) - k);
    END IF;
  END LOOP;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION public.prism_preserve_manual_meta() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.prism_preserve_manual_meta() TO service_role;
DROP TRIGGER IF EXISTS prism_preserve_manual_meta ON public.prism_contents;
CREATE TRIGGER prism_preserve_manual_meta BEFORE UPDATE OF item_meta ON public.prism_contents
  FOR EACH ROW EXECUTE FUNCTION public.prism_preserve_manual_meta();

-- Preserve prior golden versions even when a legacy caller replaces via DELETE/INSERT.
CREATE TABLE IF NOT EXISTS public.prism_golden_history (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  team_id uuid REFERENCES public.prism_teams(id) ON DELETE CASCADE,
  content_hash text NOT NULL, content jsonb NOT NULL, expected jsonb NOT NULL,
  source text, recorded_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.prism_golden_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.prism_golden_history FROM PUBLIC, anon, authenticated;
REVOKE ALL ON SEQUENCE public.prism_golden_history_id_seq FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON public.prism_golden_history TO service_role;
GRANT USAGE, SELECT ON SEQUENCE public.prism_golden_history_id_seq TO service_role;
CREATE OR REPLACE FUNCTION public.prism_archive_golden()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
BEGIN
  IF OLD.team_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.prism_teams WHERE id = OLD.team_id) THEN
    RETURN OLD;
  END IF;
  INSERT INTO public.prism_golden_history(team_id, content_hash, content, expected, source)
  VALUES (OLD.team_id, OLD.content_hash, OLD.content, OLD.expected, OLD.source);
  RETURN OLD;
END;
$$;
REVOKE ALL ON FUNCTION public.prism_archive_golden() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.prism_archive_golden() TO service_role;
DROP TRIGGER IF EXISTS prism_archive_golden ON public.prism_golden;
CREATE TRIGGER prism_archive_golden BEFORE DELETE ON public.prism_golden
  FOR EACH ROW EXECUTE FUNCTION public.prism_archive_golden();
-- UPDATE needs NEW returned, so keep its archival work in an AFTER trigger.
DROP TRIGGER IF EXISTS prism_archive_golden_update ON public.prism_golden;
CREATE TRIGGER prism_archive_golden_update AFTER UPDATE OF content, expected ON public.prism_golden
  FOR EACH ROW WHEN (OLD.content IS DISTINCT FROM NEW.content OR OLD.expected IS DISTINCT FROM NEW.expected)
  EXECUTE FUNCTION public.prism_archive_golden();
NOTIFY pgrst, 'reload schema';
COMMIT;
