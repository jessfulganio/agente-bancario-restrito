"""
banco.py — Lógica de negócio do Agente Bancário Restrito.

TODA a regra de negócio (validação + alteração de saldo) vive aqui.
O modelo de linguagem nunca calcula nem altera o saldo: ele só chama
estas funções e repassa o resultado ao usuário.

Decisões de projeto:
- Dinheiro é representado com Decimal (nunca float), evitando erros
  como 0.1 + 0.2 = 0.30000000000000004.
- Valores aceitam no máximo 2 casas decimais; nada é arredondado
  silenciosamente — valor com 3+ casas é REJEITADO.
- Toda função retorna um dict padronizado {ok, mensagem, ...} para que
  o modelo apenas repasse a mensagem pronta (sem reformatar números).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

SALDO_INICIAL = Decimal("1000.00")
LIMITE_POR_OPERACAO = Decimal("1000000.00")  # teto de sanidade por operação
CENTAVO = Decimal("0.01")

# Nome do destinatário: letras (com acento), espaços, ponto, hífen e apóstrofo.
_RE_DESTINATARIO = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ .'\-]{0,59}$")


class ErroValidacao(ValueError):
    """Erro de entrada do usuário (mensagem pode ser exibida como está)."""


def formatar_brl(valor: Decimal) -> str:
    """Formata Decimal no padrão brasileiro: R$ 1.234,56."""
    texto = f"{valor:,.2f}"  # 1,234.56
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def validar_valor(bruto) -> Decimal:
    """
    Converte e valida um valor monetário vindo da ferramenta.

    Aceita: str ("250", "250.5", "250.50") ou int/float vindos do JSON.
    Rejeita: booleanos, vazio, texto não numérico, NaN/infinito,
    zero, negativos, mais de 2 casas decimais e valores acima do limite.
    """
    if isinstance(bruto, bool) or bruto is None:
        raise ErroValidacao("O valor informado não é numérico.")
    if isinstance(bruto, (int, float)):
        bruto = str(bruto)
    if not isinstance(bruto, str):
        raise ErroValidacao("O valor informado não é numérico.")

    texto = bruto.strip()
    # Formato estrito: sinal opcional, dígitos, ponto decimal opcional.
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?", texto):
        raise ErroValidacao(
            "O valor informado não é numérico. Use apenas números, ex.: 250 ou 250.50."
        )
    try:
        valor = Decimal(texto)
    except InvalidOperation:
        raise ErroValidacao("O valor informado não é numérico.")

    if not valor.is_finite():
        raise ErroValidacao("O valor informado não é numérico.")
    if valor <= 0:
        raise ErroValidacao("O valor deve ser maior que zero.")
    if valor.as_tuple().exponent < -2 and valor != valor.quantize(CENTAVO):
        raise ErroValidacao("O valor pode ter no máximo 2 casas decimais (centavos).")
    if valor > LIMITE_POR_OPERACAO:
        raise ErroValidacao(
            f"O valor excede o limite por operação de {formatar_brl(LIMITE_POR_OPERACAO)}."
        )
    return valor.quantize(CENTAVO)  # normaliza 250 -> 250.00 (sem perda)


def validar_destinatario(bruto) -> str:
    """Valida o nome do titular da conta de destino (simulada)."""
    if not isinstance(bruto, str) or not bruto.strip():
        raise ErroValidacao("Informe o nome do destinatário da transferência.")
    nome = " ".join(bruto.split())  # normaliza espaços
    if not _RE_DESTINATARIO.fullmatch(nome):
        raise ErroValidacao(
            "Nome de destinatário inválido. Use apenas letras e espaços (até 60 caracteres)."
        )
    return nome


@dataclass
class Conta:
    """Estado da única conta fictícia da sessão."""

    titular: str = "Conta do aluno"
    saldo: Decimal = SALDO_INICIAL
    historico: list = field(default_factory=list)  # trilha de auditoria interna

    def _registrar(self, tipo: str, valor: Decimal, detalhe: str = "") -> None:
        self.historico.append(
            {
                "quando": datetime.now().isoformat(timespec="seconds"),
                "tipo": tipo,
                "valor": str(valor),
                "detalhe": detalhe,
                "saldo_apos": str(self.saldo),
            }
        )

    # ------------------------------------------------------------------
    # As três operações permitidas
    # ------------------------------------------------------------------
    def consultar_saldo(self) -> dict:
        return {
            "ok": True,
            "saldo": str(self.saldo),
            "mensagem": f"Seu saldo atual é {formatar_brl(self.saldo)}.",
        }

    def depositar(self, valor) -> dict:
        try:
            v = validar_valor(valor)
        except ErroValidacao as e:
            return {"ok": False, "erro": str(e), "saldo": str(self.saldo),
                    "mensagem": f"Depósito não realizado: {e} Saldo inalterado: {formatar_brl(self.saldo)}."}
        self.saldo = self.saldo + v
        self._registrar("deposito", v)
        return {
            "ok": True,
            "valor": str(v),
            "saldo": str(self.saldo),
            "mensagem": f"Depósito de {formatar_brl(v)} realizado com sucesso. "
                        f"Novo saldo: {formatar_brl(self.saldo)}.",
        }

    def transferir(self, valor, destinatario) -> dict:
        try:
            v = validar_valor(valor)
            nome = validar_destinatario(destinatario)
        except ErroValidacao as e:
            return {"ok": False, "erro": str(e), "saldo": str(self.saldo),
                    "mensagem": f"Transferência não realizada: {e} Saldo inalterado: {formatar_brl(self.saldo)}."}
        if v > self.saldo:
            return {
                "ok": False,
                "erro": "saldo_insuficiente",
                "saldo": str(self.saldo),
                "mensagem": f"Transferência não realizada: saldo insuficiente. "
                            f"Você tentou transferir {formatar_brl(v)}, mas seu saldo é "
                            f"{formatar_brl(self.saldo)}.",
            }
        self.saldo = self.saldo - v
        assert self.saldo >= 0, "Invariante violada: saldo negativo"  # defesa extra
        self._registrar("transferencia", v, nome)
        return {
            "ok": True,
            "valor": str(v),
            "destinatario": nome,
            "saldo": str(self.saldo),
            "mensagem": f"Transferência realizada. Origem: {self.titular}. "
                        f"Destino: conta de {nome} (simulada). Valor: {formatar_brl(v)}. "
                        f"Novo saldo: {formatar_brl(self.saldo)}.",
        }
