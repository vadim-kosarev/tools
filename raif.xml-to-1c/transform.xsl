<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="2.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:foo="http://www.foo.org/"
                xmlns:bar="http://www.bar.org"
                xmlns:xs="http://www.w3.org/2001/XMLSchema">
    <xsl:output method="text" encoding="Cp1251" />
    <xsl:template match="/"><![CDATA[1CClientBankExchange
ВерсияФормата=1.03
Кодировка=Windows
Отправитель=iSimpleBank 2.0
Получатель=ExternalProg
РасчСчет=40817810901102599839
СекцияРасчСчет
РасчСчет=40817810901102599839
КонецРасчСчет]]>
        <xsl:apply-templates select="./OFX/BANKMSGSRSV1/STMTTRNRS/STMTRS/BANKTRANLIST/STMTTRN"/>
    </xsl:template>
    <xsl:template match="STMTTRN">
        <xsl:variable name="accID" select="../../BANKACCTFROM/ACCTID/text()" />
<![CDATA[СекцияДокумент=Платежное поручение]]>
<![CDATA[Номер=]]><xsl:value-of select="./CHECKNUM"/>
<![CDATA[НазначениеПлатежа=]]><xsl:value-of select="./NAME" />
        <xsl:variable name="strDatePosted" select="./DTPOSTED"/>
        <xsl:variable name="datePosted"><xsl:value-of select="substring($strDatePosted,7,2)"/>.<xsl:value-of select="substring($strDatePosted,5,2)"/>.<xsl:value-of select="substring($strDatePosted,1,4)"/></xsl:variable>
<![CDATA[Дата=]]><xsl:value-of select="$datePosted"/>
        <xsl:variable name="amnt" select="number(./TRNAMT)"/>
        <xsl:variable name="absAmnt">
            <xsl:choose>
                <xsl:when test="$amnt &lt; 0"><xsl:value-of select="-$amnt"/></xsl:when>
                <xsl:otherwise><xsl:value-of select="$amnt"/></xsl:otherwise>
            </xsl:choose>
        </xsl:variable>
<![CDATA[Сумма=]]><xsl:value-of select="$absAmnt"/>
        <xsl:if test="./TRNTYPE/text() = 'DEBIT'">
<![CDATA[ПлательщикБИК=044525700
ПлательщикБанк1=АО "Райффайзенбанк" Г. Москва
ПлательщикКорсчет=30101810200000000700
Плательщик1=Индивидуальный предприниматель Косарев Вадим Анатольевич
ПлательщикИНН=525623227950
ПлательщикСчет=]]><xsl:value-of select="$accID" />
        </xsl:if>
        <xsl:if test="./TRNTYPE/text() = 'CREDIT'">
<![CDATA[ПолучательБИК=044525700
ПолучательБанк1=АО "Райффайзенбанк" Г. Москва
ПолучательКорсчет=30101810200000000700
Получатель1=Индивидуальный предприниматель Косарев Вадим Анатольевич
ПолучательИНН=525623227950
ПолучательСчет=]]><xsl:value-of select="$accID" />
        </xsl:if>
<![CDATA[КонецДокумента]]>
    </xsl:template>
</xsl:stylesheet>
