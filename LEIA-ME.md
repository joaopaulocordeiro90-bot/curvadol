# Curva DOL — 100% automática (GitHub)

Tudo num lugar só: a **página** + o **robô** que busca os dados todo dia + o agendador.
Não precisa de servidor, não precisa pagar nada.

## O que cada arquivo faz
- `index.html` — a página da curva (é o que você abre).
- `robot.py` — o robô: busca pré (ANBIMA) + cupom (B3) e grava no `curve.json`.
- `curve.json` — onde o robô deixa os números prontos pra página ler.
- `.github/workflows/update.yml` — o agendador: roda o robô sozinho todo dia.
- `requirements.txt` — peças que o robô precisa.

## Como ligar (uma vez só, ~10 min)

1. Cria uma conta grátis no **github.com** (se não tiver).
2. **New repository** → nome `curva-dol` → marca **Public** → **Create**.
3. Na página do repositório: **Add file → Upload files** → arrasta TODOS estes
   arquivos (inclusive a pasta `.github`) → **Commit changes**.
4. Liga o site: **Settings → Pages** → em "Source" escolhe **Deploy from a branch**
   → branch **main** / pasta **/ (root)** → **Save**. Em 1 min ele te dá o link
   `https://SEU-USUARIO.github.io/curva-dol/`.
5. Liga o robô: aba **Actions** → se pedir, clica **I understand... enable** →
   escolhe "Atualiza curva DOL" → botão **Run workflow** (roda a primeira vez na hora).

Pronto. A partir daí o robô roda sozinho todo dia depois do fechamento, e o link
sempre mostra a curva atualizada. **Você só abre e olha.**

## Como saber se está funcionando
- Na aba **Actions**, cada execução vira uma linha verde (ok) ou vermelha (erro).
- Abrindo o link, o rodapé mostra a data dos dados.
