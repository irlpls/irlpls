"""Prolific: vetted online participants. commission() creates a draft study, then publishes it only with confirm=True.
Token in PROLIFIC_API_TOKEN. Remote tasks only; Prolific does not send people to places.

Docs: https://docs.prolific.com/ (POST /study-cost-calculator/ {reward, total_available_places} -> total_cost; POST
/studies/ creates an UNPUBLISHED draft; POST /studies/{id}/transition/ {action: PUBLISH} spends money; GET /studies/{id}/;
'Authorization: Token <token>')."""
from .. import http
from .base import Adapter, pick_operation, prepared_call

BASE = "https://api.prolific.com/api/v1"


class Prolific(Adapter):
    card_id = "prolific"
    env = "PROLIFIC_API_TOKEN"
    how_to_get = "Prolific researcher account → API Tokens (app.prolific.com/researcher/tokens/)."
    can_commission = True

    def _h(self, required=True):
        return {"Authorization": f"Token {self.key(required)}"}

    def prepare(self, req, route, operation=None):
        """quote: the study cost calculator (default); order: POST /studies/ (a draft; publishing is a separate, paid step);
        status: one study."""
        op = pick_operation(route, operation, "commission", ("quote", "order", "status"), "quote")
        if not op:
            return None
        h = {"Authorization": "Token {" + self.env + "}"}
        auth = dict(auth_kind="api_key_header", key_name=self.env, key_in="header Authorization: Token <token>",
                    get_key_url="https://app.prolific.com/researcher/tokens/", docs_url="https://docs.prolific.com/")
        if op == "status":
            return prepared_call(self.card_id, "status", "GET", f"{BASE}/studies/{{STUDY_ID}}/", headers=h, **auth,
                                 placeholders=[{"name": "STUDY_ID", "what": "the study id POST /studies/ returned", "where": "its response"}],
                                 notes="Submissions: GET /studies/{id}/submissions/.")
        places = req.get("quantity") or 1
        reward = {"name": "REWARD_CENTS", "what": "each participant's reward in cents (or pence) of the account currency",
                  "where": "your user's budget"}
        h = dict(h, **{"Content-Type": "application/json"})
        if op == "quote":
            return prepared_call(self.card_id, "quote", "POST", f"{BASE}/study-cost-calculator/", headers=h,
                                 body={"reward": "{REWARD_CENTS}", "total_available_places": places}, **auth,
                                 placeholders=[reward],
                                 notes="Returns total_cost including VAT and fees, in the account currency; creates nothing.")
        need = req.get("need") or "Study"
        body = self._body({"name": need[:120], "description": need, "external_study_url": "{STUDY_URL}", "places": places,
                           "reward_cents": "{REWARD_CENTS}"})
        return prepared_call(
            self.card_id, "order", "POST", f"{BASE}/studies/", headers=h, body=body, **auth,
            placeholders=[reward, {"name": "STUDY_URL", "what": "your study's web address (Prolific appends the participant id)",
                                   "where": "your study tool"}],
            notes="Creates an UNPUBLISHED draft; nothing is spent. POST /studies/{id}/transition/ with action PUBLISH spends "
                  "real money from the workspace (no sandbox) and needs the person's approval. Remote participants only.")

    @staticmethod
    def _body(spec):
        return {"name": spec["name"], "description": spec.get("description", spec["name"]),
                "external_study_url": spec["external_study_url"], "prolific_id_option": "url_parameters",
                "total_available_places": spec.get("places", 10), "estimated_completion_time": spec.get("minutes", 5),
                "reward": spec.get("reward_cents", 100),
                "completion_codes": [{"code": spec.get("completion_code", "IRLPLS"), "code_type": "COMPLETED", "actions": [{"action": "AUTOMATICALLY_APPROVE"}]}]}

    def commission(self, spec, confirm=False):
        """spec: name, description, external_study_url, places, minutes, reward_cents, completion_code."""
        body = self._body(spec)
        if not confirm:
            return self._dry("POST", f"{BASE}/studies/", body, self._h(required=False))
        study = http.post(f"{BASE}/studies/", body=body, headers=self._h())
        http.post(f"{BASE}/studies/{study['id']}/transition/", body={"action": "PUBLISH"}, headers=self._h())
        return study

    def status(self, order):
        sid = order["id"]
        study = http.get(f"{BASE}/studies/{sid}/", headers=self._h())
        subs = http.get(f"{BASE}/studies/{sid}/submissions/", headers=self._h())
        return {"study": study, "submissions": subs.get("results", subs)}
