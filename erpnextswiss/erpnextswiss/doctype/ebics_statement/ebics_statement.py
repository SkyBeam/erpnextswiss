# -*- coding: utf-8 -*-
# Copyright (c) 2024, libracore (https://www.libracore.com) and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from erpnextswiss.erpnextswiss.page.bank_wizard.bank_wizard import read_camt053_meta, read_camt053, make_payment_entry, get_default_accounts
from frappe import _
import json

class ebicsStatement(Document):
    def parse_content(self):
        """
        Read the xml content and parse into the doctype record
        """
        if not self.xml_content:
            frappe.throw( _("Cannot parse this file: {0}. No content found.").format(self.name) )
            
        # read meta data
        meta = read_camt053_meta(self.xml_content)
        self.update({
            'currency': meta.get('currency'),
            'opening_balance': meta.get('opening_balance'),
            'closing_balance': meta.get('closing_balance')
        })
        account_matches = frappe.db.sql("""
            SELECT `name`
            FROM `tabAccount`
            WHERE `iban` = "{iban}" AND `account_type` = "Bank";
            """.format(iban=meta.get('iban')), as_dict=True)
        print("{0}".format(account_matches))
        if len(account_matches) > 0:
            self.account = account_matches[0]['name']
        
            # read transactions (only if account is available)
            transactions = read_camt053(self.xml_content, self.account)
            
            print("Txns: {0}".format(transactions))
            # read transactions into the child table
            for transaction in transactions.get('transactions'):
                print("{0}".format(transaction))
                # stringify lists to store them in child table
                if transaction.get("invoice_matches"):
                    transaction['invoice_matches'] = "{0}".format(transaction.get("invoice_matches"))
                if transaction.get("expense_matches"):
                    transaction['expense_matches'] = "{0}".format(transaction.get("expense_matches"))
                
                transactions['status'] = "Pending"
                
                self.append("transactions", transaction)
                
        else:
            frappe.log_error( _("Unable to find matching account: please check your accounts and set IBAN {0}").format(meta.get('iban')), _("ebics statement parsing failed") )
        
        # save
        self.save()
        frappe.db.commit()
        
        return

    def process_transactions(self):
        """
        Analyse transactions and if possible, match them
        """
        default_accounts = get_default_accounts(bank_account=self.account)
        
        for t in self.transactions:
            # if matched amount equals the transaction amount, create and submit payment
            if t.matched_amount == t.amount and t.party_match:
                payment = {
                    'amount': t.amount,
                    'date': t.date,
                    'reference_no': t.unique_reference,
                    'party_iban': t.party_iban
                }
                if t.credit_debit == "DBIT":
                    # outflow: purchase invoice or expense
                    payment.update({
                        'paid_from': self.account,
                        'paid_to': default_accounts.get('payable_account'),
                        'type': "Pay",
                        'party_type': "Supplier",
                        'party': t.party_match,
                        'references': json.loads(t.invoice_matches),
                        'remarks': "{0}, {1}, {2}".format(t.transaction_reference or "", t.party_name or "", t.party_address or ""),
                        'auto_submit': 1,
                        'company': self.company
                    })
                else:
                    # inflow: debtor
                    payment.update({
                        'paid_from': default_accounts.get('receivable_account'),
                        'paid_to': self.account,
                        'type': "Receive",
                        'party_type': "Customer",
                        'party': t.party_match,
                        'references': json.loads(t.invoice_matches),
                        'remarks': "{0}, {1}, {2}".format(t.transaction_reference or "", t.party_name or "", t.party_address or ""),
                        'auto_submit': 1,
                        'company': self.company
                    })
                
                make_payment_entry(payment)
                
        return
