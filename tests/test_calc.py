def calcular():
    print("=" * 35)
    print("       CALCULADORA EM PYTHON")
    print("=" * 35)

    while True:
        try:
            numero1 = float(input("\nDigite o primeiro número: "))

            operador = input(
                "Digite a operação (+, -, *, /): "
            ).strip()

            if operador not in ["+", "-", "*", "/"]:
                print("❌ Operação inválida!")
                continue

            numero2 = float(input("Digite o segundo número: "))

            if operador == "+":
                resultado = numero1 + numero2

            elif operador == "-":
                resultado = numero1 - numero2

            elif operador == "*":
                resultado = numero1 * numero2

            elif operador == "/":
                if numero2 == 0:
                    print("❌ Não é possível dividir por zero!")
                    continue

                resultado = numero1 / numero2

            # Remove .0 quando o resultado for inteiro
            if resultado.is_integer():
                resultado = int(resultado)

            print(f"\n✅ Resultado: {resultado}")

        except ValueError:
            print("❌ Digite apenas números válidos!")

        continuar = input("\nDeseja fazer outra conta? (s/n): ").strip().lower()

        if continuar != "s":
            print("\nCalculadora encerrada. Até mais!")
            break


calcular()
