"""Central Atendimento: authorizations, safety, ML API contracts, and AI drafts."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers import support


@pytest.fixture
def account(monkeypatch):
    seller = SimpleNamespace(mercado_livre_user_id=123, user_id=5)
    async def valid_token(connection, db):
        assert connection is seller
        return "fake-access-token"
    def owned(db, user_id, account_id):
        assert user_id == 5
        if account_id != 123:
            raise HTTPException(404, "Outra conta")
        return seller
    monkeypatch.setattr(support, "get_account", owned)
    monkeypatch.setattr(support, "get_valid_access_token", valid_token)
    return SimpleNamespace(user=SimpleNamespace(id=5), seller=seller)


def test_strict_reference_and_reply_validation():
    assert support._ref("messages", "1234567890") == "1234567890"
    for value in ("../admin", "123?to=9", "abc", "0/123", "-1"):
        with pytest.raises(HTTPException):
            support._ref("messages", value)
    assert support._clean_text(" ok ", "questions") == "ok"
    with pytest.raises(HTTPException):
        support._clean_text("X" * 351, "messages")
    with pytest.raises(HTTPException):
        support._clean_text("Olá 😀", "messages")
    with pytest.raises(ValidationError):
        support.ReplyRequest(account_id=123,kind="messages",reference="5",text="ok",confirmed=False)


@pytest.mark.asyncio
async def test_questions_list_enforces_tenant_and_filters_foreign_seller(monkeypatch, account):
    seen = []
    async def fake_call(token, method, path, params=None, payload=None):
        seen.append((method,path,params))
        return {"questions":[
            {"id": 112, "seller_id":123,"text":"Tem nota?","status":"UNANSWERED","item_id":"MLB123"},
            {"id": 113, "seller_id":999,"text":"Segredo","status":"UNANSWERED"},
        ]}
    monkeypatch.setattr(support, "_call", fake_call)
    results = await support.list_threads(account_id=123,kind="questions",limit=20,user=account.user,db=object())
    assert [x["reference"] for x in results["threads"]] == ["112"]
    assert seen[0][2]["seller_id"] == 123
    with pytest.raises(HTTPException) as error:
        await support.list_threads(account_id=999,kind="questions",limit=20,user=account.user,db=object())
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_messages_list_does_not_mark_read_and_ignores_other_sellers(monkeypatch, account):
    seen=[]
    async def fake_call(token, method, path, params=None, payload=None):
        seen.append((path,params))
        if path == "/messages/unread":
            return {"results":[
                {"resource":"/packs/888/sellers/123", "count":2},
                {"resource":"/packs/999/sellers/999", "count":2},
                {"resource":"/packs/../../secrets/sellers/123", "count":9},
            ]}
        return {"results":[{"seller":{"id":123},"id":777,"pack_id":444},
                           {"seller":{"id":999},"id":123456}]}
    monkeypatch.setattr(support,"_call",fake_call)
    result=await support.list_threads(account_id=123,kind="messages",limit=10,user=account.user,db=object())
    assert [t["reference"] for t in result["threads"]] == ["888","444"]
    assert result["threads"][0]["unread"] is True
    assert seen[0][1]["tag"] == "post_sale"


@pytest.mark.asyncio
async def test_detail_never_marks_read(monkeypatch, account):
    seen=[]
    async def fake_call(token,method,path,params=None,payload=None):
        seen.append((path,params))
        return {"conversation_status":{"status":"active"},
                "messages":[{"id":"m1","from":{"user_id":111},
                             "text":{"plain":"Pedido não chegou."}}]}
    monkeypatch.setattr(support,"_call",fake_call)
    result=await support.detail(reference="888",account_id=123,kind="messages",user=account.user,db=object())
    assert result["can_reply"] is True
    assert result["messages"][0]["from_seller"] is False
    assert seen[0][1]["mark_as_read"] == "false"


@pytest.mark.asyncio
async def test_question_reply_requires_owner_and_approval_and_audits(monkeypatch, account):
    class DB:
        def __init__(self): self.rows = []
        def add(self,item):
            item.id=77
            self.rows.append(item)
        def commit(self): pass
    db=DB()
    sent=[]
    async def fake_detail(token,kind,reference,seller_id):
        return {"can_reply":True}
    async def fake_call(token,method,path,params=None,payload=None):
        sent.append((method,path,payload))
        return {"id":"reply-1"}
    monkeypatch.setattr(support,"_detail",fake_detail)
    monkeypatch.setattr(support,"_call",fake_call)
    request=support.ReplyRequest(account_id=123,kind="questions",reference="12",
                                 text="Disponível.",confirmed=True)
    result=await support.reply(body=request,user=account.user,db=db)
    assert result["sent"] and result["audit_id"]==77
    assert sent==[("POST","/answers",{"question_id":12,"text":"Disponível."})]
    assert db.rows[0].user_id==5
    assert db.rows[0].seller_id==123
    request.account_id=999
    with pytest.raises(HTTPException):
        await support.reply(body=request,user=account.user,db=db)
    assert len(sent)==1


@pytest.mark.asyncio
async def test_blocked_conversation_never_sends(monkeypatch,account):
    async def blocked(token,kind,reference,seller_id):
        return {"can_reply":False}
    async def forbidden(*args,**kwargs):
        raise AssertionError("No provider POST permitted")
    monkeypatch.setattr(support,"_detail",blocked)
    monkeypatch.setattr(support,"_call",forbidden)
    body=support.ReplyRequest(account_id=123,kind="messages",reference="77",
                              text="Mensagem",confirmed=True)
    with pytest.raises(HTTPException) as error:
        await support.reply(body=body,user=account.user,db=object())
    assert error.value.status_code==409


@pytest.mark.asyncio
async def test_ai_not_configured_does_not_send_or_invent(monkeypatch,account):
    from app.config.settings import settings
    monkeypatch.setattr(settings,"openai_api_key",None)
    async def available(token,kind,reference,seller_id):
        return {"can_reply":True,"messages":[{"from_seller":False,"text":"Meu pedido?"}]}
    monkeypatch.setattr(support,"_detail",available)
    with pytest.raises(HTTPException) as error:
        await support.suggest(body=support.SuggestRequest(account_id=123,kind="messages",reference="77"),
                              user=account.user,db=object())
    assert error.value.status_code==503


@pytest.mark.asyncio
async def test_post_sale_2026_agents_use_official_mlb_recipient(monkeypatch, account):
    class DB:
        def add(self,item): item.id=15
        def commit(self): pass
    async def fake_detail(token,kind,reference,seller_id):
        return {"can_reply":True,"conversation_path":"/packs/77/sellers/123/conversations/post_sale"}
    sent=[]
    async def fake_call(token,method,path,params=None,payload=None):
        if path == "/users/me":
            return {"id":123,"site_id":"MLB"}
        sent.append((method,path,params,payload))
        return {"id":"sent"}
    monkeypatch.setattr(support,"_detail",fake_detail)
    monkeypatch.setattr(support,"_call",fake_call)
    body=support.ReplyRequest(account_id=123,kind="messages",reference="77",
                              text="O pedido está em trânsito.",confirmed=True)
    result=await support.reply(body=body,user=account.user,db=DB())
    assert result["sent"] is True
    assert sent[0][1]=="/messages/packs/77/sellers/123"
    assert sent[0][3]["to"]["user_id"]==3037675074
    assert sent[0][2]=={"tag":"post_sale"}


@pytest.mark.asyncio
async def test_ai_suggestions_are_only_drafts_and_no_external_tokens_exposed(monkeypatch, account):
    from pydantic import SecretStr
    from app.config.settings import settings
    monkeypatch.setattr(settings,"openai_api_key",SecretStr("fake-key"))
    async def fake_detail(token,kind,reference,seller_id):
        return {"can_reply":True,"reference":reference,
                "messages":[{"from_seller":False,"text":"Tem nota fiscal?"}]}
    monkeypatch.setattr(support,"_detail",fake_detail)
    original=httpx.AsyncClient
    def respond(req):
        assert req.url.host=="api.openai.com"
        assert req.headers["Authorization"]=="Bearer fake-key"
        import json
        body=json.loads(req.content)
        assert body["store"] is False
        assert "Tem nota fiscal?" in body["input"]
        return httpx.Response(200,json={"output":[{"content":[
          {"type":"output_text","text":"Olá! Consulte os documentos do seu pedido."}]}]})
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    result=await support.suggest(body=support.SuggestRequest(account_id=123,kind="questions",reference="66"),
                                 user=account.user,db=object())
    assert result["requires_approval"] is True
    assert "Olá!" in result["suggestion"]
    assert "fake-key" not in str(result)
